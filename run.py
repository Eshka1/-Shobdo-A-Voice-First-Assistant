import os
from app import create_app
from app.services.scheduler_service import start_scheduler

app = create_app()

if __name__ == '__main__':
    # TEMPORARY development server. Risk: the debug reloader/debugger must never face the internet.
    # Production (Day 3) uses a real WSGI server and ProductionConfig (DEBUG=False).
    debug = bool(app.config.get('DEBUG'))
    # The debug reloader starts this file twice (parent + child). Only the child has
    # WERKZEUG_RUN_MAIN=true, so start the scheduler there only: never two schedulers.
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        start_scheduler(app)
    app.run(debug=debug, host='127.0.0.1', port=5000)
