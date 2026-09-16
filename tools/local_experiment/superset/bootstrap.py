"""Initialize only this experiment's SQLite metadata and generated local admin."""
import json
from pathlib import Path

from superset.app import create_app

private = json.loads(Path('/app/superset_home/local-private.json').read_text())
app = create_app()
with app.app_context():
    manager = app.appbuilder.sm
    if not manager.find_user(username='local-admin'):
        manager.add_user(username='local-admin', first_name='Local', last_name='Experiment',
                         email='local-admin@localhost.invalid',
                         role=manager.find_role('Admin'), password=private['admin_password'])
print('Local experiment admin initialized; credentials retained only in private state.')
