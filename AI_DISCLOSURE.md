# AI Disclosure

## Tool
- Claude (Anthropic)

## How it was used
- Drafting the Streamlit app, the Prometheus instrumentation (metrics.py), the processing logic and unit tests.
- Generating the Grafana dashboard JSON (scripts/build_dashboard.py) following the 7 plotting commandments.
- Debugging PromQL queries and Prometheus label behaviour (pre-initialising label series at 0 so rate() counts the first event).

## What I did myself
- Set up and ran Prometheus, node_exporter and Grafana on my machine, and verified all scrape targets are UP.
- Ran the app with the real BLIP model, generated the traffic, recorded the dashboard video and exported the dashboard.
