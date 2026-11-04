# Public planning dashboard

The default `/dashboard/` entry point is the map-first EnerMap planning interface, hosted publicly on GitHub Pages. It runs Leaflet and Plotly directly in the browser and requires no account, Python download or server.

See [PLANNING_INTERFACE.md](PLANNING_INTERFACE.md) for views, data sources, export commands and verification.

The original Streamlit implementation remains at `streamlit.html`, powered by @stlite/browser 1.12.0. Its `mount-options.json` pins app/data URLs to the tested original public snapshot. This separate research explorer is not the default public interface.
