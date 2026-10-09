# Results page

`docs/index.html` is a self-contained page, including Plotly and figures, built with `python docs/make_current_site.py`. GitHub Pages serves `main:/docs`. The Streamlit app reads the same packaged aggregates. Rebuilding the modelling outputs requires the separately held input data and environment; the dashboard requires only the repository requirements.
