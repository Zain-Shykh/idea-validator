from agentops.api.routes import router
from common.service import create_app

app = create_app("agentops")
app.include_router(router)
