from common.service import create_app
from ideation.api.routes import router

app = create_app("ideation")
app.include_router(router)
