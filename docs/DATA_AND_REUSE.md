# Data and reuse

The dashboard contains selected aggregate CSV/JSON outputs and static PNG/PDF/SVG figures from outputs/current. Source and packaged hashes are recorded in snapshot_manifest.json. Excluded material includes raw EPC registers, UPRN/TOID tables, dwelling-level economics, household microdata, MasterMap geometry files, rasters, weather and simulation checkpoints. Static maps are retained as research figures; original provider terms still apply.

Research notebooks are review copies with saved static outputs. Identifier-bearing output tables, interactive payloads, long reports and personal paths are omitted where detected. A saved error is explicitly recorded, not replaced by a successful result. notebook_manifest.json records source hashes and transformations. Unchanged upstream and solar notebooks retain their earlier review treatment.

The first code cell blocks accidental Run All unless ENERMAP_ENABLE_MODEL_RUN=1 is deliberately set. This is a presentation guard, not a security boundary. Actual rerunning also requires omitted source inputs, third-party engines and the original environment. The Streamlit dashboard needs only requirements.txt and the packaged summaries.

The code and local pyBuildingEnergy patch are available for inspection. This private repository grants no new redistribution rights for third-party data or software. Keep the repository private. No live public deployment or collaborator invitation is performed by the packaging script.
