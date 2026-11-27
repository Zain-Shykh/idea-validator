from common.service import create_app
from critic.api.routes import router

app = create_app("critic")
app.include_router(router)
