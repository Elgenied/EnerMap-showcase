# Data and reuse

Included data are the eleven small saved aggregate tables/parameter files required by the dashboard, plus selected saved static figures. There are no raw EPC registers, UPRN/address linkage tables, household microdata, MasterMap geometries, LiDAR rasters, weather files or dwelling-level model tables.

The notebooks are review copies: scientific calculations were not executed during packaging. Cached static PNG charts are retained verbatim. Identifier-bearing tables, large reports, executable interactive maps and personal paths were removed where detected; an error output is replaced by an explicit error notice, never by a successful result. See `notebook_manifest.json` for the changes. The footprint notebook had no cached outputs at source.

The first cell of each research notebook stops accidental Run All unless `ENERMAP_ENABLE_MODEL_RUN=1` is deliberately set. This is a presentation guard, not a security boundary. Rerunning also requires the omitted source inputs and the original modelling environment. Upstream notebooks retain their original relative data-layout assumptions.

The runnable component is the Streamlit results explorer. It reads the included saved summaries and figures and performs display aggregation only. It does not simulate buildings, train clusters or calibrate parameters.

Third-party engines and microdata are omitted. The first-party integration code and local pyBuildingEnergy patch are included for inspection. Obtain pyBuildingEnergy and pybuildingcluster with their respective licences to reproduce modelling; inspect the local patch and source audit first. Data products retain their original provider terms. No open-source licence or permission to redistribute third-party data is granted by this private review repository.
