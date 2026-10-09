import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pandas as pd
from dash import Dash, dcc, html, Input, Output
import dash_ag_grid as dag
import plotly.express as px
import dash_bootstrap_components as dbc
from databricks import sql
from databricks.sdk.core import Config

CATALOG = 'samples'
ROW_LIMIT = 5

# Auth comes from the app's service principal when deployed, or your CLI profile locally
cfg = Config()
# Set in app.yaml from the app's "sql-warehouse" resource
WAREHOUSE_ID = os.getenv('DATABRICKS_WAREHOUSE_ID')


def run_query(query):
    with sql.connect(
        server_hostname=cfg.host,
        http_path=f'/sql/1.0/warehouses/{WAREHOUSE_ID}',
        credentials_provider=lambda: cfg.authenticate,
    ) as conn, conn.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


@lru_cache(maxsize=1)
def list_tables():
    """Return {schema: [table, ...]} for every table in the catalog."""
    df = run_query(f"""
        SELECT table_schema, table_name
        FROM {CATALOG}.information_schema.tables
        WHERE table_schema <> 'information_schema'
        ORDER BY table_schema, table_name
    """)
    return df.groupby('table_schema')['table_name'].apply(list).to_dict()


@lru_cache(maxsize=32)
def top_rows(schema, table):
    df = run_query(f'SELECT * FROM `{CATALOG}`.`{schema}`.`{table}` LIMIT {ROW_LIMIT}')
    # Dates, decimals and nested types aren't JSON-friendly, so show everything as text
    return df.astype(str)


def table_card(schema, table):
    try:
        df = top_rows(schema, table)
        body = dag.AgGrid(
            rowData=df.to_dict('records'),
            columnDefs=[{'field': c} for c in df.columns],
            defaultColDef={'resizable': True, 'sortable': True, 'minWidth': 120},
            dashGridOptions={'domLayout': 'autoHeight'},
            style={'height': None},
        )
    except Exception as e:
        body = dbc.Alert(f'Could not query this table: {e}', color='warning')
    return dbc.Card([
        dbc.CardHeader(html.Code(f'{CATALOG}.{schema}.{table}')),
        dbc.CardBody(body),
    ], className='mb-4')


chart_data = pd.DataFrame({'x': [x for x in range(30)],
                           'y': [2 ** x for x in range(30)]})

# Initialize the Dash app with Bootstrap styling
dash_app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

# Define the app layout
dash_app.layout = dbc.Container([
    dbc.Row([dbc.Col(html.H1('Hello Forevernew Databricks testing'), width=12)]),
    dcc.Graph(
        id='fare-scatter',
        figure=px.scatter(chart_data, x='x', y='y',
            labels={'x': 'Apps', 'y': 'Fun with data'},
            template='simple_white'),
        style={'height': '500px', 'width': f'{min(100 + 50 * 30, 1000)}px'}
    ),
    html.H2(f'Explore the {CATALOG} catalog', className='mt-4'),
    html.P(f'Pick a schema to see the top {ROW_LIMIT} rows of every table in it.'),
    dcc.Dropdown(id='schema-dropdown', placeholder='Select a schema', className='mb-4'),
    dcc.Loading(html.Div(id='tables-container')),
], fluid=True)


@dash_app.callback(
    Output('schema-dropdown', 'options'),
    Input('schema-dropdown', 'id'),
)
def load_schemas(_):
    tables = list_tables()
    return [{'label': f'{s} ({len(t)} tables)', 'value': s} for s, t in tables.items()]


@dash_app.callback(
    Output('tables-container', 'children'),
    Input('schema-dropdown', 'value'),
)
def show_tables(schema):
    if not schema:
        return None
    # Query the tables in parallel; one at a time is slow for schemas with ~24 tables
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda t: table_card(schema, t), list_tables()[schema]))


if __name__ == '__main__':
    dash_app.run(debug=True)
