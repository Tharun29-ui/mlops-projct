"""AWS Lambda entry point for the FastAPI application.

Keep this module at the root of the packaged Lambda asset because the configured
handler is ``lambda_handler.handler``.
"""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
