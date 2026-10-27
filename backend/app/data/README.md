# Private analysis assets are not distributed

The deployed application's real invoice extracts and model bundle are excluded
because they contain private transaction histories and product records.
The public API remains usable for local invoice and inventory workflows.
Packaged real-analysis routes may return an unavailable response until you train
and provide your own private artifacts. Never commit those records.

For an isolated reproducible ML demonstration, use
ml/datasets/synthetic-model-demo/RUN.ps1 from the repository root.
It writes only the separate synthetic dataset and results.
