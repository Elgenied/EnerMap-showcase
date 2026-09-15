"""
Small utilities used by the Guildford pybuildingcluster notebooks.

The core workflow calls the EURAC library directly (ClusteringAnalyzer,
RegressionModelBuilder, SensitivityAnalyzer); the functions here handle data
plumbing, the parallel k-scans, the Li & Dogan (2025) archetype method used in
notebook 01b (mirroring EnerMap's `archetype_lib.py`), and plots the library
does not provide.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================================ generic
def setup_plots() -> None:
    plt.rcParams.update({
        "figure.dpi": 110, "font.size": 9, "axes.grid": True, "grid.alpha": 0.25,
        "axes.spines.top": False, "axes.spines.right": False, "figure.max_open_warning": 0,
    })
    pd.set_option("display.width", 180)
    pd.set_option("display.max_columns", 80)


def sanitize(name: str) -> str:
    """Column-name safe for LightGBM/XGBoost/JSON: letters, digits, underscore."""
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(name)).strip("_")
    return re.sub(r"_+", "_", s)


def hdd_from_epw(path: Path, base_c: float = 15.5) -> float:
    """Annual heating degree-days (daily-mean method) from an EPW file."""
    epw = pd.read_csv(path, skiprows=8, header=None)
    t = epw[6].astype(float).to_numpy()            # dry-bulb temperature, column 7 of the EPW record
    daily = t[: len(t) // 24 * 24].reshape(-1, 24).mean(axis=1)
    return float(np.clip(base_c - daily, 0, None).sum())


def impute_group_median(df: pd.DataFrame, cols: list[str], by: str) -> pd.DataFrame:
    """Fill NaNs with the median of the same `by` group, then the global median."""
    out = df.copy()
    for c in cols:
        grp = out.groupby(by, observed=True)[c].transform("median")
        out[c] = out[c].fillna(grp).fillna(out[c].median())
    return out


def feature_columns_regression(df: pd.DataFrame, target: str, leakage: list[str]) -> list[str]:
    """EURAC's `feature_columns_regression`: everything numeric that is not a target/leak."""
    drop = set(leakage) | {target, "cluster"}
    return [c for c in df.columns
            if c not in drop and pd.api.types.is_numeric_dtype(df[c]) and df[c].nunique() > 1]


def mode_or_none(s: pd.Series):
    s = s.dropna()
    return None if s.empty else s.mode().iloc[0]


def purity(df: pd.DataFrame, group: str, col: str) -> pd.Series:
    """Share of the dominant category of `col` inside each group."""
    return df.groupby(group)[col].agg(lambda s: s.value_counts(normalize=True).iloc[0])


def json_safe(o):
    """Recursively convert numpy / pandas scalars for json.dump."""
    if isinstance(o, dict):
        return {str(k): json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_safe(v) for v in o]
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and np.isnan(o):
        return None
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


# ============================================================================ notebook 01 (demand k-scan)
def k_scan(X: np.ndarray, k_range=(2, 15), seed: int = 42, n_jobs: int = -1,
           silhouette_sample: int | None = 20_000) -> pd.DataFrame:
    """
    Score every candidate k in parallel (one process per k).

    Returns inertia (elbow), silhouette, Calinski-Harabasz, Davies-Bouldin and the
    share of the smallest cluster - the decision table for choosing k.
    """
    from joblib import Parallel, delayed
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score, calinski_harabasz_score, davies_bouldin_score

    ks = list(range(k_range[0], k_range[1] + 1))

    def one(k):
        km = KMeans(n_clusters=k, random_state=seed, n_init=10).fit(X)
        lab = km.labels_
        sizes = np.bincount(lab) / len(lab)
        ss = min(silhouette_sample or len(X), len(X))
        return dict(
            k=k, inertia=km.inertia_,
            silhouette=silhouette_score(X, lab, sample_size=ss, random_state=seed),
            calinski_harabasz=calinski_harabasz_score(X, lab),
            davies_bouldin=davies_bouldin_score(X, lab),
            smallest_cluster_share=sizes.min(),
        )

    workers = min(n_jobs if n_jobs and n_jobs > 0 else len(ks), len(ks))
    rows = Parallel(n_jobs=workers)(delayed(one)(k) for k in ks)
    return pd.DataFrame(rows).set_index("k")


def plot_k_scan(scan: pd.DataFrame, chosen: int | None = None, save: Path | None = None):
    fig, axes = plt.subplots(1, 4, figsize=(16, 3.6))
    panels = [("inertia", "Inertia (elbow)"), ("silhouette", "Silhouette (higher = better)"),
              ("calinski_harabasz", "Calinski-Harabasz (higher = better)"),
              ("davies_bouldin", "Davies-Bouldin (lower = better)")]
    for ax, (col, title) in zip(axes, panels):
        ax.plot(scan.index, scan[col], "o-", ms=4)
        if chosen is not None:
            ax.axvline(chosen, color="crimson", ls="--", lw=1, label=f"k = {chosen}")
            ax.legend()
        ax.set_title(title, fontsize=9); ax.set_xlabel("k")
    plt.tight_layout()
    if save:
        fig.savefig(save, bbox_inches="tight")
    plt.show()


# ============================================================================ notebook 01b (Li & Dogan archetypes)
class FeatureEncoder:
    """
    EnerMap's `_matrix` design matrix kept as an object so it can be re-applied later
    (medoids in notebook 04): one-hot categoricals + median-imputed, winsorised (0.5-99.5 %),
    optionally log1p-transformed, standardised numerics.
    """

    def __init__(self, cat_cols, num_cols, log_cols=()):
        self.cat_cols, self.num_cols, self.log_cols = list(cat_cols), list(num_cols), set(log_cols)
        self.bounds: dict[str, tuple[float, float]] = {}
        self.ct = None
        self.feature_names: list[str] = []

    def _prepare(self, df: pd.DataFrame, fit: bool) -> pd.DataFrame:
        X = df[self.cat_cols + self.num_cols].copy()
        for c in self.cat_cols:
            X[c] = X[c].astype("object").fillna("Unknown").astype(str)
        for c in self.num_cols:
            v = pd.to_numeric(X[c], errors="coerce")
            if c in self.log_cols:
                v = np.log1p(v.clip(lower=0))
            if fit:
                self.bounds[c] = (float(v.quantile(0.005)), float(v.quantile(0.995)))
            lo, hi = self.bounds[c]
            X[c] = v.clip(lo, hi)
        return X

    def fit_transform(self, df: pd.DataFrame) -> np.ndarray:
        from sklearn.compose import ColumnTransformer
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler
        X = self._prepare(df, fit=True)
        transformers = []
        if self.cat_cols:
            transformers.append(("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), self.cat_cols))
        if self.num_cols:
            transformers.append(("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                                                  ("sc", StandardScaler())]), self.num_cols))
        self.ct = ColumnTransformer(transformers)
        M = self.ct.fit_transform(X)
        self.feature_names = list(self.ct.get_feature_names_out())
        return M

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return self.ct.transform(self._prepare(df, fit=False))


def silhouette_capped(X, labels, seed=42, cap=10_000):
    """Silhouette on a random sample (EnerMap's `_silhouette`, cap raised from 4,000)."""
    from sklearn.metrics import silhouette_score
    labels = np.asarray(labels)
    if len(np.unique(labels)) < 2:
        return np.nan
    if X.shape[0] > cap:
        rng = np.random.default_rng(seed)
        idx = rng.choice(X.shape[0], cap, replace=False)
        if len(np.unique(labels[idx])) < 2:
            return np.nan
        return silhouette_score(X[idx], labels[idx])
    return silhouette_score(X, labels)


def pred_accuracy(known_X: np.ndarray, labels, seed: int = 42, n_splits: int = 3, cap: int = 15_000,
                  n_jobs: int = 1) -> float:
    """
    Li & Dogan's second criterion (EnerMap `_pred_accuracy`): cross-validated accuracy of
    recovering the cluster label from the known attributes with a random forest.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score
    from sklearn.model_selection import StratifiedKFold
    labels = np.asarray(labels)
    if len(np.unique(labels)) < 2:
        return 1.0
    if known_X.shape[0] > cap:
        rng = np.random.default_rng(seed)
        idx = rng.choice(known_X.shape[0], cap, replace=False)
        known_X, labels = known_X[idx], labels[idx]
    clf = RandomForestClassifier(n_estimators=150, random_state=seed, n_jobs=n_jobs)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    accs = []
    for tr, te in skf.split(known_X, labels):
        clf.fit(known_X[tr], labels[tr])
        accs.append(accuracy_score(labels[te], clf.predict(known_X[te])))
    return float(np.mean(accs))


def _fit(algo: str, X: np.ndarray, k: int, seed: int):
    from sklearn.cluster import KMeans
    from sklearn.mixture import GaussianMixture
    if algo == "kmeans":
        return KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
    return GaussianMixture(n_components=k, covariance_type="diag", reg_covar=1e-4,
                           random_state=seed, n_init=3).fit(X)


def stability(algo: str, X: np.ndarray, k: int, ref_labels: np.ndarray, seed: int = 42,
              n_boot: int = 5, frac: float = 0.8) -> float:
    """
    Bootstrap stability (von Luxburg 2010): re-fit on random subsamples, predict every row,
    and measure agreement with the full-data solution by the adjusted Rand index. 1 = the
    partition is reproducible; values below ~0.8 mean the split is driven by sampling noise.
    """
    from sklearn.metrics import adjusted_rand_score
    rng = np.random.default_rng(seed)
    n = X.shape[0]
    scores = []
    for b in range(n_boot):
        idx = rng.choice(n, int(frac * n), replace=False)
        m = _fit(algo, X[idx], k, seed + 1 + b)
        scores.append(adjusted_rand_score(ref_labels, m.predict(X)))
    return float(np.mean(scores))


def scan_stratum(X: np.ndarray, known_X: np.ndarray | None, k_range=(2, 12), seed: int = 42,
                 n_jobs: int = -1, cap: int = 10_000, n_boot: int = 5, frac: float = 0.8):
    """
    EnerMap's `scan_clusters` (K-Means: SSD; GMM-diag: BIC; both: silhouette, smallest cluster,
    prediction accuracy from known attributes) plus bootstrap stability, in parallel over
    (algorithm, k).  Returns (criteria DataFrame, {(algo, k): labels}, {(algo, k): model}).
    """
    from joblib import Parallel, delayed

    jobs = [(a, k) for k in range(k_range[0], k_range[1] + 1) for a in ("kmeans", "gmm")]

    def one(algo, k):
        m = _fit(algo, X, k, seed)
        lab = m.predict(X)
        row = dict(algo=algo, k=k,
                   ssd=float(m.inertia_) if algo == "kmeans" else np.nan,
                   bic=float(m.bic(X)) if algo == "gmm" else np.nan,
                   silhouette=silhouette_capped(X, lab, seed, cap),
                   min_cluster=int(np.bincount(lab, minlength=k).min()),
                   stability=stability(algo, X, k, lab, seed, n_boot, frac),
                   pred_acc=pred_accuracy(known_X, lab, seed) if known_X is not None else np.nan)
        return row, lab, m

    workers = min(n_jobs if n_jobs and n_jobs > 0 else len(jobs), len(jobs))
    out = Parallel(n_jobs=workers)(delayed(one)(a, k) for a, k in jobs)
    criteria = pd.DataFrame([r for r, _, _ in out]).sort_values(["algo", "k"]).reset_index(drop=True)
    labels_store = {(r["algo"], r["k"]): lab for r, lab, _ in out}
    models_store = {(r["algo"], r["k"]): m for r, _, m in out}
    return criteria, labels_store, models_store


def elbow_k(series: pd.Series) -> int:
    """k at the maximum discrete curvature of a decreasing criterion (EnerMap's `_elbow_k`)."""
    s = series.dropna().sort_index()
    if len(s) < 3:
        return int(s.index[0])
    v = (s - s.min()) / max(s.max() - s.min(), 1e-12)
    curv = v.shift(1) - 2 * v + v.shift(-1)
    return int(curv.dropna().idxmax())


def select_clustering(criteria: pd.DataFrame, min_cluster: int = 300, stability_min: float = 0.8) -> dict:
    """
    Deterministic selection rule with the structure of EnerMap's `select_clustering`
    (Li & Dogan's revealed preference: maximum resolution subject to gates).  The assignability
    gate is replaced by a stability gate, because every dwelling here already carries its
    attributes and nothing is assigned; assignability is still reported as a diagnostic.

    1. candidates: K-Means at the SSD elbow, GMM at the BIC minimum and the BIC elbow, the
       smallest scanned k of each algorithm, and - the resolution candidates - the largest k
       of each algorithm whose smallest cluster >= `min_cluster` and stability >= `stability_min`;
    2. drop candidates with a cluster smaller than `min_cluster` (if none survive, keep all);
    3. keep candidates with stability >= `stability_min` (fallback: >= stability_min - 0.1; else all);
    4. rank by LARGEST k, tie-break higher stability, then higher silhouette.
    """
    c = criteria.set_index(["algo", "k"])
    km = criteria[criteria.algo == "kmeans"].set_index("k")
    gm = criteria[criteria.algo == "gmm"].set_index("k")
    cand: set[tuple[str, int]] = set()
    if len(km):
        cand.add(("kmeans", elbow_k(km["ssd"])))
        cand.add(("kmeans", int(km.index.min())))
    if len(gm):
        cand.add(("gmm", int(gm["bic"].idxmin())))
        cand.add(("gmm", elbow_k(gm["bic"])))
        cand.add(("gmm", int(gm.index.min())))
    for algo, tab in (("kmeans", km), ("gmm", gm)):
        ok = tab[(tab["min_cluster"] >= min_cluster) & (tab["stability"] >= stability_min)]
        if len(ok):
            cand.add((algo, int(ok.index.max())))
    rows = [(a, k, c.loc[(a, k)]) for a, k in sorted(cand) if (a, k) in c.index]
    sized = [r for r in rows if r[2]["min_cluster"] >= min_cluster]
    rows = sized or rows
    gated = [r for r in rows if r[2]["stability"] >= stability_min]
    relaxed = False
    if not gated:
        gated = [r for r in rows if r[2]["stability"] >= stability_min - 0.1]
        relaxed = True
    ok = gated or rows
    ok.sort(key=lambda r: (-r[1], -round(r[2]["stability"], 3), -round(r[2]["silhouette"], 3)))
    a, k, row = ok[0]
    return {"algo": a, "k": int(k), "silhouette": float(row["silhouette"]), "stability": float(row["stability"]),
            "pred_acc": float(row["pred_acc"]) if pd.notna(row["pred_acc"]) else np.nan,
            "min_cluster": int(row["min_cluster"]), "gate_relaxed": relaxed,
            "candidates": [(x[0], int(x[1])) for x in rows]}


def plot_criteria(criteria: pd.DataFrame, sel: dict, title: str, min_cluster: int, stability_min: float,
                  pred_acc_ref: float = 0.7, save: Path | None = None):
    km = criteria[criteria.algo == "kmeans"].set_index("k")
    gm = criteria[criteria.algo == "gmm"].set_index("k")
    fig, axes = plt.subplots(1, 5, figsize=(20, 3.4))
    axes[0].plot(km.index, km["ssd"], "o-", ms=4); axes[0].set_title("K-Means SSD (elbow)")
    axes[1].plot(gm.index, gm["bic"], "s-", ms=4, color="darkorange"); axes[1].set_title("GMM-diag BIC (min)")
    for ax, col, ttl in ((axes[2], "silhouette", "silhouette (10k sample)"),
                         (axes[3], "stability", "bootstrap stability (ARI) - GATE"),
                         (axes[4], "pred_acc", "assignability from known attributes - diagnostic")):
        ax.plot(km.index, km[col], "o-", ms=4, label="K-Means"); ax.plot(gm.index, gm[col], "s-", ms=4, label="GMM")
        ax.set_title(ttl, fontsize=9); ax.legend(fontsize=7)
    axes[3].axhline(stability_min, color="grey", ls=":", lw=1)
    axes[4].axhline(pred_acc_ref, color="grey", ls=":", lw=1)
    for ax in axes:
        ax.axvline(sel["k"], color="crimson", ls="--", lw=1); ax.set_xlabel("k")
    for _, r in criteria[criteria["min_cluster"] < min_cluster].iterrows():
        axes[3].plot(r["k"], r["stability"], "x", color="red", ms=6)
    fig.suptitle(f"{title}: selected {sel['algo']} k={sel['k']}  (silhouette {sel['silhouette']:.2f}, stability {sel['stability']:.2f}, "
                 f"assignability {sel['pred_acc']:.2f}{', gate relaxed' if sel.get('gate_relaxed') else ''})"
                 f"   red x = a cluster < {min_cluster} dwellings", fontsize=10)
    plt.tight_layout()
    if save:
        fig.savefig(save, bbox_inches="tight")
    plt.show()


def characterise(df: pd.DataFrame, labels: pd.Series, cat_cols: list[str], num_cols: list[str]) -> pd.DataFrame:
    """
    Li & Dogan characterisation (EnerMap `characterise`): modal category with its share for every
    categorical attribute; median as the initial guess and p5-p95 as the feasible range for every
    continuous attribute.
    """
    d = df.copy(); d["_label"] = labels.values
    n_all = int(labels.notna().sum())
    recs = []
    for lab, sub in d.groupby("_label", sort=True):
        r = {"label": lab, "n": len(sub), "share": len(sub) / n_all}
        for c in cat_cols:
            vc = sub[c].astype("object").fillna("Unknown").value_counts(normalize=True)
            r[f"{c}__mode"] = vc.index[0]; r[f"{c}__share"] = float(vc.iloc[0])
        for c in num_cols:
            v = pd.to_numeric(sub[c], errors="coerce").dropna()
            r[f"{c}__median"] = float(v.median()) if len(v) else np.nan
            r[f"{c}__p5"] = float(v.quantile(0.05)) if len(v) else np.nan
            r[f"{c}__p95"] = float(v.quantile(0.95)) if len(v) else np.nan
        recs.append(r)
    return pd.DataFrame(recs).set_index("label")


def characterisation_json(char: pd.DataFrame, cat_cols: list[str], num_cols: list[str], names: dict) -> dict:
    """EnerMap `characterisation_json`: {label: {name, n, share, categorical{}, continuous{}}}."""
    out = {}
    for lab, r in char.iterrows():
        out[lab] = {
            "name": names.get(lab, lab), "n": int(r["n"]), "share": round(float(r["share"]), 4),
            "categorical": {c: {"value": r[f"{c}__mode"], "share": round(float(r[f"{c}__share"]), 3)} for c in cat_cols},
            "continuous": {c: {"initial_guess": r[f"{c}__median"],
                               "feasible_range": [r[f"{c}__p5"], r[f"{c}__p95"]]} for c in num_cols},
        }
    return json_safe(out)


def name_construction(r: pd.Series, age_label: dict) -> str:
    """Readable name like EnerMap's: 'Uninsulated cavity, double glazing, 1930-49 (wall U 1.50)'."""
    ins = str(r["wall_insulation__mode"]).lower()
    wall = str(r["wall_construction_resolved__mode"]).lower()
    glz = str(r["glazing_type__mode"]).split("/")[0].lower()
    roof = str(r["roof_type__mode"]).lower()
    band = age_label.get(str(r.get("age_band_resolved__mode", "")), "")
    return (f"{ins} {wall}, {glz} glazing, {roof} roof, {band} "
            f"(wall U {r['wall_U__median']:.2f}, roof U {r['roof_U__median']:.2f}, window U {r['window_U__median']:.1f})")


def dedupe_names(names: dict) -> dict:
    """Make names unique by suffixing duplicates with their label."""
    seen: dict[str, int] = {}
    out = {}
    for lab, nm in names.items():
        seen[nm] = seen.get(nm, 0) + 1
        out[lab] = nm if seen[nm] == 1 else f"{nm} [{lab}]"
    return out


def bimodality_flag(x: pd.Series, min_share: float = 0.2, delta_bic: float = 10.0) -> dict:
    """
    Is a 2-component Gaussian mixture clearly better than 1 for this distribution, with both
    components carrying at least `min_share`?  A *hint* that an archetype hides two demand
    populations (an occupancy split, not a fabric one).
    """
    from sklearn.mixture import GaussianMixture
    v = np.log(x.dropna().clip(lower=1).to_numpy()).reshape(-1, 1)
    if len(v) < 100:
        return {"bimodal": False, "delta_bic": np.nan, "minor_share": np.nan}
    g1 = GaussianMixture(1, random_state=0).fit(v)
    g2 = GaussianMixture(2, random_state=0, n_init=3).fit(v)
    d = g1.bic(v) - g2.bic(v)
    minor = float(g2.weights_.min())
    return {"bimodal": bool(d > delta_bic and minor >= min_share), "delta_bic": float(d), "minor_share": minor}


# ============================================================================ profiles / plots
def cluster_profile(labels: pd.DataFrame, cluster_col: str, cat_cols: list[str]) -> dict[str, pd.DataFrame]:
    """Row-normalised composition of each cluster for a list of categorical columns."""
    return {c: (pd.crosstab(labels[cluster_col], labels[c], normalize="index") * 100).round(1)
            for c in cat_cols}


def plot_profiles(profiles: dict[str, pd.DataFrame], ncols: int = 3, save: Path | None = None):
    n = len(profiles)
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.6 * ncols, 3.4 * nrows))
    for ax, (name, tab) in zip(np.ravel(axes), profiles.items()):
        tab.plot.bar(stacked=True, ax=ax, width=0.8, colormap="tab20", legend=False)
        ax.set_title(name, fontsize=9); ax.set_ylabel("% of cluster"); ax.set_xlabel("cluster")
        ax.tick_params(axis="x", rotation=0)
        ax.legend(fontsize=6, ncol=1, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    for ax in np.ravel(axes)[n:]:
        ax.axis("off")
    plt.tight_layout()
    if save:
        fig.savefig(save, bbox_inches="tight")
    plt.show()
