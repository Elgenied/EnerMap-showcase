# Browser deployment

This entry point runs the public Streamlit `app.py` using @stlite/browser 1.12.0. No Python server or credentials are required. GitHub Pages serves this directory. `mount-options.json` pins all app/data URLs to a public repository commit so the dashboard is internally consistent.

To refresh: update the pinned commit in mount-options.json after publishing a complete, tested app/results snapshot. Include any new file dependencies. Test the browser app before publishing the changed manifest.

The native Streamlit app remains available using `streamlit run app.py`. Browser execution downloads Python packages and 14.2 MB of app/results assets on the first visit; allow time for initial loading. All selected assets were already public in the repository.
