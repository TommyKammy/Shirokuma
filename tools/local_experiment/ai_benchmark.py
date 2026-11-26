#!/usr/bin/env python3
"""Business SQL result benchmark against the resident Iceberg marts / tpch_tiny.

The shared CTE contract maps the resident TPC-H names to business names. TPC-H
F means completed; O/P remain open/pending. CANCELLED/REFUNDED are preserved if
present. Empty loss sets yield zero. Item revenue follows the requested business
formula (including quantity), rather than the standard TPC-H revenue formula.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
from decimal import Decimal, localcontext
import json
import re
import sys

import warehouse_resident as wr

from ai_config import _ai_completion, merged_ai_config


SOURCE_SQL = """WITH orders AS (
 SELECT orderkey AS order_id, custkey AS customer_id, orderdate AS order_date,
        totalprice AS total_amount,
        CASE orderstatus WHEN 'F' THEN 'COMPLETED' WHEN 'O' THEN 'OPEN'
             WHEN 'P' THEN 'PENDING' ELSE orderstatus END AS status
 FROM iceberg.marts.fct_orders
), customers AS (
 SELECT custkey AS customer_id, customer_name, nation_name AS nation
 FROM iceberg.marts.dim_customers
), items AS (
 SELECT orderkey AS order_id, quantity, extendedprice AS extended_price, discount
 FROM iceberg.tpch_tiny.lineitem
), priorities AS (
 SELECT orderkey AS order_id, orderpriority AS order_priority
 FROM iceberg.tpch_tiny.orders
)
"""


def _entry(number, en, ja, sql, description):
    return dict(id=f'business-{number:02d}', question=dict(en=en, ja=ja),
                gold_sql=SOURCE_SQL + sql, description=description)


BENCHMARKS = [
    _entry(1, 'What is gross revenue from completed orders?', '完了した注文の総売上は？',
           "SELECT COALESCE(SUM(total_amount), 0) AS revenue FROM orders WHERE status = 'COMPLETED'",
           'Sum order totals once, filtering completed status.'),
    _entry(2, 'Who are the top 5 customers by completed order revenue? Break ties by customer ID.',
           '完了注文の売上上位5顧客は？同額は顧客ID順。',
           "SELECT c.customer_id, c.customer_name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON c.customer_id = o.customer_id WHERE o.status = 'COMPLETED' GROUP BY c.customer_id, c.customer_name ORDER BY revenue DESC, c.customer_id LIMIT 5",
           'Customer/order join preserves order grain; deterministic top five.'),
    _entry(3, 'What is completed order revenue by month?', '月別の完了注文売上は？',
           "SELECT date_trunc('month', order_date) AS month, SUM(total_amount) AS revenue FROM orders WHERE status = 'COMPLETED' GROUP BY 1 ORDER BY 1",
           'Calendar month aggregation using date_trunc.'),
    _entry(4, 'What is average order value for completed orders?', '完了注文の平均注文額は？',
           "SELECT AVG(total_amount) AS aov FROM orders WHERE status = 'COMPLETED'",
           'Average at order grain, not customer or item grain.'),
    _entry(5, 'What is the total lost amount of cancelled and refunded orders?', 'キャンセル・返金注文の損失合計は？',
           "SELECT COALESCE(SUM(total_amount), 0) AS loss FROM orders WHERE status IN ('CANCELLED', 'REFUNDED')",
           'Loss includes both statuses; no matching orders yields zero.'),
    _entry(6, 'Count orders and sum revenue by order priority and status for all orders.', '全注文の優先度・状態別の件数と売上は？',
           'SELECT p.order_priority, o.status, COUNT(*) AS order_count, SUM(o.total_amount) AS revenue FROM orders o JOIN priorities p ON o.order_id = p.order_id GROUP BY 1, 2 ORDER BY 1, 2',
           'Both grouping dimensions, with one priority row per order.'),
    _entry(7, 'How many customers are in each nation, including customers without orders?', '注文のない顧客も含めた国別顧客数は？',
           'SELECT nation, COUNT(*) AS customer_count FROM customers GROUP BY nation ORDER BY nation',
           'Customer grain; does not multiply customers by orders.'),
    _entry(8, 'Which customer IDs have completed order revenue greater than 1000?', '完了注文の売上が1000を超える顧客IDと売上は？',
           "SELECT customer_id, SUM(total_amount) AS revenue FROM orders WHERE status = 'COMPLETED' GROUP BY customer_id HAVING SUM(total_amount) > 1000 ORDER BY customer_id",
           'Strict aggregate threshold in HAVING.'),
    _entry(9, 'Count distinct customers with completed orders from 1994-01-01 inclusive to 1995-01-01 exclusive.', '1994年1月1日以上、1995年1月1日未満に完了注文した顧客の重複なし件数は？',
           "SELECT COUNT(DISTINCT customer_id) AS customer_count FROM orders WHERE status = 'COMPLETED' AND order_date >= DATE '1994-01-01' AND order_date < DATE '1995-01-01'",
           'Distinct customer count with explicit half-open date interval.'),
    _entry(10, 'What is total item quantity for each customer with orders?', '注文した顧客ごとの商品数量合計は？',
           'SELECT o.customer_id, SUM(i.quantity) AS quantity FROM orders o JOIN items i ON o.order_id = i.order_id GROUP BY o.customer_id ORDER BY o.customer_id',
           'Aggregate quantities at item grain across all statuses.'),
    _entry(11, 'What is completed order net item revenue using quantity * extended_price * (1 - discount)?', 'quantity * extended_price * (1 - discount) による完了注文の純売上は？',
           "SELECT COALESCE(SUM(i.quantity * i.extended_price * (1 - i.discount)), 0) AS net_revenue FROM items i JOIN orders o ON i.order_id = o.order_id WHERE o.status = 'COMPLETED'",
           'Requested business item formula, including quantity and discount.'),
    _entry(12, 'How many orders were placed on each order date, across all statuses?', '全状態の注文日別注文数は？',
           'SELECT order_date, COUNT(*) AS order_count FROM orders GROUP BY order_date ORDER BY order_date',
           'Daily order counts at order grain.'),
    _entry(13, 'What is the total tax on completed orders at a rate of 10% of total_amount?', '完了注文のtotal_amountに10%を掛けた税額合計は？',
           "SELECT COALESCE(SUM(total_amount * DECIMAL '0.1'), 0) AS tax FROM orders WHERE status = 'COMPLETED'",
           'Sum tax at order grain using a decimal rate.'),
    _entry(14, 'Show the distribution of all order counts per customer, including zero-order customers.', '注文ゼロの顧客も含め、顧客ごとの注文数の分布は？',
           'SELECT order_count, COUNT(*) AS customer_count FROM (SELECT c.customer_id, COUNT(o.order_id) AS order_count FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id GROUP BY c.customer_id) counts GROUP BY order_count ORDER BY order_count',
           'Two-stage count with a left join to include zero-order customers.'),
    _entry(15, 'Compare yearly completed order revenue totals.', '年別の完了注文売上合計を比較して。',
           "SELECT year(order_date) AS year, SUM(total_amount) AS revenue FROM orders WHERE status = 'COMPLETED' GROUP BY 1 ORDER BY 1",
           'Yearly revenue totals with completed status filter.'),
]


def sql(url, statement, max_rows=None):
    headers = {"X-Trino-User": "shirokuma-bears", "X-Trino-Source": "shirokuma-bears",
               "X-Trino-Catalog": "iceberg", "X-Trino-Schema": "marts"}
    if max_rows is not None:
        return wr.execute(url, statement, headers, max_rows=max_rows)
    # Honor explicit larger limits used by exports while keeping ordinary calls
    # and their default result cap unchanged.
    limit = re.search(r'\bLIMIT ([0-9]+)\s*$', statement)
    if limit and int(limit[1]) > 1000:
        return wr.execute(url, statement, headers, max_rows=int(limit[1]))
    return wr.execute(url, statement, headers)


def generate_data_sql(url, request, llm_usage=None, sources_rows=None, config=None):
    sources = sql(url, "SELECT table_schema, table_name, column_name, data_type FROM iceberg.information_schema.columns WHERE table_schema IN ('marts', 'tpch_tiny', 'raw', 'sandbox')", max_rows=10000)
    if sources_rows is not None:
        sources_rows.extend(sources['rows'])
    return _complete_data_sql(dict(request=request, sources=sources['rows']), llm_usage, config)


def _extract_sql(content: str) -> str:
    content = content.strip()
    fence = re.search(r'```(?:sql|json)?\s*\n?(.*?)```', content, re.I | re.S)
    if fence:
        content = fence[1].strip()
    try:
        parsed = json.loads(content)
    except ValueError:
        statement = content
    else:
        if not isinstance(parsed, dict) or not isinstance(parsed.get('generated_sql'), str):
            raise ValueError('completion must contain generated_sql')
        statement = parsed['generated_sql']
    return statement.strip().removesuffix(';').rstrip()


def _complete_data_sql(context, llm_usage=None, config=None):
    active = merged_ai_config(config or {})
    body = {"model": active['model'], "temperature": active['temperature'],
            "max_tokens": active['max_tokens'],
            "messages": [{"role": "system", "content": "Return JSON with generated_sql: one SELECT statement (WITH allowed). Use only supplied Iceberg sources. No DDL or DML. Use Trino ANSI SQL only (no QUALIFY; wrap window functions in a CTE and filter with WHERE). Correct failed_sql using error_message when supplied. Business semantic definitions (apply only to supplied columns): Completed sales require status = 'COMPLETED'; exclude 'CANCELLED' and 'REFUNDED'. Revenue uses total_amount at order grain, or quantity * extended_price * (1 - discount) at item grain. Time-grain aggregations use date_trunc('month', order_date), or the requested day/year grain. Grain-safe joins: avoid duplicate row multiplication when joining orders, order_items, or customers; aggregate items to order grain before summing order totals and use unique customer keys."},
                         {"role": "user", "content": json.dumps(context)}]}
    if active['provider'] == 'local-openai':
        body['enable_thinking'] = False
    completion = _ai_completion(active, body)
    if llm_usage is not None and (completion.get('model') or completion.get('usage')):
        usage = completion.get('usage') or {}
        llm_usage.update(model_provider=active['provider'], model_name=completion.get('model') or body['model'])
        for key in ('prompt_tokens', 'completion_tokens'):
            count = usage.get(key)
            if type(count) is int and count >= 0:
                llm_usage[key] = (llm_usage.get(key) or 0) + count
            else:
                llm_usage.setdefault(key, None)
    return _extract_sql(completion["choices"][0]["message"]["content"])


def results_match(actual, expected):
    """Compare row multisets, ignoring aliases/order; numbers round to 6 places.

    Preserve duplicate rows, NULLs, column positions and textual values. Trino
    DECIMAL JSON strings are numeric only when their column type says so.
    Never accept a comparison of truncated result prefixes.
    """
    if actual.get('truncated') or expected.get('truncated'):
        return False
    if len(actual['columns']) != len(expected['columns']):
        return False

    def canonical(result):
        def cell(value, column):
            if value is None:
                return ('null',)
            numeric = column['type'].lower().startswith(('decimal', 'double', 'real', 'bigint', 'integer', 'smallint', 'tinyint'))
            if numeric:
                number = Decimal(str(value))
                if not number.is_finite():
                    return ('nonfinite', str(number))
                with localcontext() as ctx:
                    ctx.prec = max(50, len(number.as_tuple().digits) + abs(number.adjusted()) + 10)
                    return ('number', number.quantize(Decimal('0.000001')))
            return ('value', json.dumps(value, sort_keys=True, ensure_ascii=False))
        rows = []
        for row in result['rows']:
            if len(row) != len(result['columns']):
                raise ValueError('row width does not match columns')
            rows.append(tuple(cell(value, col) for value, col in zip(row, result['columns'])))
        return Counter(rows)

    return canonical(actual) == canonical(expected)


def run_benchmark(url=None, mock_generator=None):
    """Run all questions; mock_generator(question_en) may supply SQL for tests.

    Generated SQL receives the same CTE contract as gold SQL via its prompt,
    without receiving the gold answer. Gold failures remain explicit in details
    and do not erase successful execution of the generated query.
    """
    details = []
    with (nullcontext(url) if url is not None else wr.trino()) as endpoint:
        for entry in BENCHMARKS:
            detail = dict(id=entry['id'], question=entry['question'], syntax_valid=False,
                          semantic_correct=False)
            actual = gold = None
            try:
                if mock_generator is not None:
                    statement = mock_generator(entry['question']['en'])
                else:
                    statement = generate_data_sql(endpoint, entry['question']['en'] +
                        '\nUse these business CTE definitions (include them in your SQL):\n' + SOURCE_SQL)
                detail['generated_sql'] = statement
                statement = wr.check_sql_policy(statement, mode='read-only')
                actual = sql(endpoint, statement, max_rows=sys.maxsize)
                detail['syntax_valid'] = True
            except Exception as exc:
                detail['error'] = str(exc)
            try:
                gold = sql(endpoint, entry['gold_sql'], max_rows=sys.maxsize)
            except Exception as exc:
                detail['gold_error'] = str(exc)
            if actual is not None and gold is not None:
                detail['semantic_correct'] = results_match(actual, gold)
                detail.update(generated_row_count=len(actual['rows']), gold_row_count=len(gold['rows']))
                if actual.get('truncated') or gold.get('truncated'):
                    detail['comparison_error'] = 'truncated results'
            details.append(detail)
    total = len(details)
    syntax = sum(d['syntax_valid'] for d in details)
    semantic = sum(d['semantic_correct'] for d in details)
    return dict(total_questions=total, syntax_passed=syntax, semantic_passed=semantic,
                syntax_success_rate=syntax / total if total else 0,
                semantic_accuracy_rate=semantic / total if total else 0, details=details)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run'])
    parser.add_argument('--url', help='Trino endpoint; otherwise use the resident tunnel')
    args = parser.parse_args(argv)
    print(json.dumps(run_benchmark(url=args.url), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
