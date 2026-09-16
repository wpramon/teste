# GAM Line Item Copier

Ferramenta para copiar configurações entre Line Items do Google Ad Manager.

Copia:

- Targeting
- Creative Placeholders
- Delivery Settings
- Frequency Caps
- Labels
- LICA

Não copia:

- Goals
- CPM
- CPC
- Budget
- Order
- Nome
- Status

## Instalação

python -m venv venv

### Windows

venv\Scripts\activate

### Linux

source venv/bin/activate

pip install -r requirements.txt

python backend/app.py

## URL

http://localhost:8080/health