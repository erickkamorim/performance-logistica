"""
FLUXO — preparação dos dados do dashboard.

1. Carrega os CSVs da Olist em um banco SQLite.
2. Cria a view de entregas (mesma regra da análise em SQL).
3. Gera a base tratada (dados/base_entregas_olist.csv) e o cubo agregado
   usado pelo painel (mês x UF x faixa de atraso x postagem do vendedor).

Uso: python preparar_dados.py <pasta_com_os_csvs_da_olist>
Dados: Brazilian E-Commerce Public Dataset by Olist (Kaggle), CC BY-NC-SA 4.0.
"""
import sys, json, sqlite3
import pandas as pd

PASTA = sys.argv[1] if len(sys.argv) > 1 else "olist"
con = sqlite3.connect(":memory:")
for t in ["orders", "order_items", "customers", "order_reviews"]:
    pd.read_csv(f"{PASTA}/olist_{t}_dataset.csv").to_sql(t, con, index=False)

con.executescript("""
CREATE VIEW vw_entregas AS
SELECT o.order_id, c.customer_state AS uf_cliente,
       STRFTIME('%Y-%m', o.order_purchase_timestamp) AS mes,
       JULIANDAY(o.order_approved_at)             - JULIANDAY(o.order_purchase_timestamp)     AS dias_aprovacao,
       JULIANDAY(o.order_delivered_carrier_date)  - JULIANDAY(o.order_approved_at)            AS dias_postagem,
       JULIANDAY(o.order_delivered_customer_date) - JULIANDAY(o.order_delivered_carrier_date) AS dias_transporte,
       JULIANDAY(o.order_delivered_customer_date) - JULIANDAY(o.order_purchase_timestamp)     AS lead_time,
       JULIANDAY(DATE(o.order_delivered_customer_date)) - JULIANDAY(DATE(o.order_estimated_delivery_date)) AS dias_vs_prazo,
       CASE WHEN DATE(o.order_delivered_customer_date) <= DATE(o.order_estimated_delivery_date) THEN 1 ELSE 0 END AS no_prazo
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_approved_at IS NOT NULL AND o.order_delivered_carrier_date IS NOT NULL
  AND o.order_delivered_customer_date IS NOT NULL AND o.order_estimated_delivery_date IS NOT NULL
  AND o.order_delivered_carrier_date >= o.order_approved_at;
""")

df = pd.read_sql("""
WITH lim AS (SELECT order_id, MIN(shipping_limit_date) lim, SUM(freight_value) frete, SUM(price) valor
             FROM order_items GROUP BY order_id),
     nota AS (SELECT order_id, AVG(review_score) nota FROM order_reviews GROUP BY order_id)
SELECT e.*, CASE WHEN o.order_delivered_carrier_date > l.lim THEN 1 ELSE 0 END AS postou_depois_limite,
       l.frete, l.valor AS valor_produtos, n.nota
FROM vw_entregas e
JOIN orders o ON o.order_id = e.order_id
JOIN lim l    ON l.order_id = e.order_id
LEFT JOIN nota n ON n.order_id = e.order_id
""", con)

df.round(2).to_csv("base_entregas_olist.csv", index=False, sep=";", decimal=",")

d = df[(df.mes >= "2017-01") & (df.mes <= "2018-08")].copy()
d["faixa"] = pd.cut(d.dias_vs_prazo, [-1e9, 0, 3, 7, 1e9], labels=[0, 1, 2, 3]).astype(int)
d["atraso"] = d.dias_vs_prazo.where(d.no_prazo == 0, 0)
d["tem_nota"] = d.nota.notna().astype(int)
g = d.groupby(["mes", "uf_cliente", "faixa", "postou_depois_limite"]).agg(
    n=("order_id", "size"), ok=("no_prazo", "sum"), lt=("lead_time", "sum"),
    ap=("dias_aprovacao", "sum"), po=("dias_postagem", "sum"), tr=("dias_transporte", "sum"),
    at=("atraso", "sum"), fr=("frete", "sum"), nn=("tem_nota", "sum"), ns=("nota", "sum")).reset_index()
meses, ufs = sorted(d.mes.unique()), sorted(d.uf_cliente.unique())
rows = [[meses.index(r.mes), ufs.index(r.uf_cliente), int(r.faixa), int(r.postou_depois_limite), int(r.n), int(r.ok),
         round(r.lt, 1), round(r.ap, 1), round(r.po, 1), round(r.tr, 1), int(r.at), round(r.fr, 1), int(r.nn), round(r.ns, 1)]
        for r in g.itertuples()]
json.dump({"months": meses, "ufs": ufs, "rows": rows}, open("cube.json", "w"), separators=(",", ":"))
print(f"{len(df)} pedidos na base; {len(d)} no painel; cubo com {len(rows)} linhas")
