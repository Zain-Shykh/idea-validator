from common.service import create_app
from verdict.api.routes import router

app = create_app("verdict")
app.include_router(router)
