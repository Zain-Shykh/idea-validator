from common.service import create_app
from scorer.api.routes import router

app = create_app("scorer")
app.include_router(router)
