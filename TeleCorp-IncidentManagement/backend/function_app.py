import azure.durable_functions as df
import azure.functions as func

from blueprints.http_api import bp as http_bp
from blueprints.triggers import bp as triggers_bp

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)
app.register_functions(http_bp)
app.register_functions(triggers_bp)
