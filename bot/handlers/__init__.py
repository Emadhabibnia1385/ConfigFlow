# handlers package — register all handlers by importing them
from . import start       # noqa: F401  registers /start
from . import callbacks   # noqa: F401  registers on_callback
from . import messages    # noqa: F401  registers universal_handler
