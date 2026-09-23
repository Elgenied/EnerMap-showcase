"""Virtual construction profiles at exact median S/M/L floor areas.

Stock records are never overwritten. IDs identify the source geometry/behaviour
template, not an assertion that the virtual inputs describe that actual dwelling.
"""
import numpy as np
import pandas as pd

METHOD = 'cluster_median_construction_three_median_areas_v1'
NUMERIC = ['wall_U', 'window_U', 'window_SHGC']
CATEGORICAL = ['wall_construction_resolved', 'wall_insulation', 'glazing_type', 'age_band_resolved']


def _mode(values):
    modes = values.dropna().mode()
    if modes.empty:
        raise ValueError(f'No typical category for {values.name}')
    return modes.iloc[0]


def virtual_profiles(inputs, representatives):
    """Return virtual rows and an audit of every overridden input.

    Numeric summaries cover the whole construction group, not each size band.
    Exposure is retained from each source template; roof properties are conditional
    on that exposure. Other geometric descriptors retain their template values.
    The engine rebuilds surface areas using the exact median floor area.
    """
    rows = inputs.loc[representatives.dwelling_id].copy()
    changes = []
    for rep in representatives.itertuples():
        group = inputs.loc[inputs.construction_sa.eq(rep.construction_sa)]
        band = group.loc[group.size_class.eq(rep.size_class)]
        profile = {c: float(group[c].median()) for c in NUMERIC}
        profile.update({c: _mode(group[c]) for c in CATEGORICAL})
        profile['age_band_ord'] = float(group.loc[group.age_band_resolved.eq(profile['age_band_resolved']), 'age_band_ord'].median())
        exposed = bool(inputs.loc[rep.dwelling_id, 'roof_exposed'])
        compatible = group.loc[group.roof_exposed.astype(bool).eq(exposed)]
        if compatible.empty or band.empty:
            raise ValueError(f'Empty representative stratum: {rep}')
        profile['roof_U'] = float(compatible.roof_U.median()) if exposed else 0.0
        profile['roof_type'] = _mode(compatible.roof_type)
        profile['floor_area_final'] = float(band.floor_area_final.median())
        if not np.isfinite([profile[c] for c in NUMERIC + ['age_band_ord', 'roof_U', 'floor_area_final']]).all():
            raise ValueError(f'Non-finite virtual profile: {rep.construction_sa}')
        if exposed and profile['roof_U'] <= 0:
            raise ValueError(f'Exposed roof has non-positive U-value: {rep.dwelling_id}')
        for field, value in profile.items():
            original = inputs.loc[rep.dwelling_id, field]
            rows.loc[rep.dwelling_id, field] = value
            changes.append(dict(dwelling_id=int(rep.dwelling_id), construction_sa=rep.construction_sa,
                size_class=rep.size_class, field=field, original=original, virtual=value,
                changed=bool(original != value)))
    unchanged = [c for c in inputs if c not in profile]
    pd.testing.assert_frame_equal(rows[unchanged], inputs.loc[rows.index, unchanged])
    return rows, pd.DataFrame(changes)
