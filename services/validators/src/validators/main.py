from common.service import create_app
from validators.api.routes import router

app = create_app("validators")
app.include_router(router)
