from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from pynubank import Nubank, HttpClient
from ynab_sdk import YNAB
from ynab_sdk.api.models.requests.transaction import TransactionRequest
import functions_framework
import os
from copy import deepcopy

@dataclass
class YNABConfig:
    # YNAB API token
    token: str
    budget_id: str
    credit_account_id: str
    checking_account_id: str
    # YYYY-MM-DD
    import_date: Optional[str] = None

    @staticmethod
    def from_env():
        return YNABConfig(
            token = os.environ['YNAB_TOKEN'],
            budget_id = os.environ['YNAB_BUDGET_ID'],
            credit_account_id = os.environ['YNAB_CREDIT_ACCOUNT_ID'],
            checking_account_id = os.environ['YNAB_CHECKING_ACCOUNT_ID'],
            import_date = os.environ.get('YNAB_IMPORT_DATE', (datetime.today() - timedelta(days = 7)).strftime('%Y-%m-%d')),
        )

@dataclass
class NubankConfig:
    cert_path: str
    cpf: str
    password: str

    @staticmethod
    def from_env():
        return NubankConfig(
            cert_path = os.environ['NUBANK_CERT_PATH'],
            cpf = os.environ['NUBANK_CPF'],
            password = os.environ['NUBANK_PASSWORD'],
        )

def collect_credit_stmts(nu: Nubank, take_while, credit_account_id):
    events = [e for e in nu.get_card_feed()['events'] if take_while(e['time'][:10])]

    # Credit card transactions
    for event in events:
        date = (datetime.fromisoformat(event['time']) - timedelta(hours = 3)).strftime('%Y-%m-%d')
        if event['category'] == 'anticipate_event':
            payee_name, amount = event['description'].split('R$')
            amount = int(amount.replace(',','')) * 10
            payee_name, *_ = payee_name.split('Você ganhou um desconto')[0].strip()
            yield TransactionRequest(
                import_id=event['id'],
                amount=int(event['description'].split('R$')[1].replace(',','')) * 10,
                payee_name=event['description'].split('R$')[0].split('Você ganhou um desconto')[0].strip(),
                memo=event['title'],
                date=date,
                account_id=credit_account_id
            )
        elif event['category'] != 'transaction':
            pass
        elif event.get('details', {}).get('charges') is None:
            yield TransactionRequest(
                import_id=event['id'],
                amount=event['amount'] * 10 * -1,
                payee_name=event['description'],
                memo=event['title'],
                date=date,
                account_id=credit_account_id,
            )
        # it is a transaction with multiple charges
        else:
            details = nu.get_card_statement_details(event)
            charges = sorted(details['transaction']['charges_list'], key=lambda x: x['index'])
            for (i, charge) in enumerate(charges):
                yield TransactionRequest(
                    import_id=event['id'].replace("-", '')+"-"+str(i+1),
                    amount=charge['amount'] * 10 * -1,
                    payee_name=event['description'],
                    date=charge['post_date'],
                    memo=f"{i+1:02}/{len(charges):02}",
                    account_id=credit_account_id,
                )


def collect_checking_stmts(nu: Nubank, take_while, checking_account_id):
    def checking_stmt_to_tx(stmt: dict) -> Optional[TransactionRequest]:
        if 'amount' not in stmt:
            return None
        amount = int(stmt['amount'] * 1000)
        payee_name = stmt['detail'].split('\n')[0]
        memo = stmt['title']

        date = stmt['postDate']
        if stmt['displayDate'] is None:
            pass
        # nubank doesn't provide the hour, so we have to do this workaround
        elif stmt['displayDate'].split(' ')[0] != date.split('-')[2]:
            date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

        if stmt['tags'] is None: # Mover dinheiro entre contas
            payee_name = stmt['title']
            title = stmt['title'].lower()
            # Mover dinheiro para fora
            if any(phrase in title for phrase in ['compra de etf', 'compra de cdb', 'compra de ações', 'reserva de ipo', 'aplica']) :
                amount = amount * -1
            elif any(phrase in title for phrase in ['transferência recebida']):
                amount = amount * +1
            else:
                return None
        elif 'payments' in stmt['tags']:  # Fatura
            payee_name = "Fatura"
            amount = amount * -1
        elif 'money-in' in stmt['tags']:  # Transferir
            amount = amount * +1
        elif 'money-out' in stmt['tags']: # Receber
            amount = amount * -1

        return TransactionRequest(
            import_id=stmt['id'],
            amount=amount,
            payee_name=payee_name,
            date=date,
            memo=memo,
            account_id=checking_account_id,
        )

    has_next_page = True
    cursor = None

    while has_next_page:
        feed = nu.get_account_feed_paginated(cursor)

        for tx in [checking_stmt_to_tx(e['node']) for e in feed['edges']]:
            if tx is None:
                continue
            if not take_while(tx.date):
                return
            yield tx

        has_next_page = feed['pageInfo']['hasNextPage']
        cursor = feed['edges'][-1]['cursor']

@functions_framework.cloud_event
def sync(message):
    # Setup YNAB
    ynab_config = YNABConfig.from_env()
    ynab = YNAB(ynab_config.token)

    def after_starting_date(tx_date):
        return datetime.strptime(tx_date, '%Y-%m-%d') >= datetime.strptime(ynab_config.import_date, '%Y-%m-%d')

    def before_today(tx: TransactionRequest):
        return datetime.strptime(tx.date, '%Y-%m-%d') <= datetime.today()

    def cap_date(tx: TransactionRequest):
        if before_today(tx):
            return tx
        else:
            tx_ = deepcopy(tx)
            tx_.date = datetime.today().strftime('%Y-%m-%d')
            return tx_

    class HttpClientWithPassword(HttpClient):
        @property
        def _cert_args(self):
            return {'pkcs12_data': self._cert, 'pkcs12_password': 'nubank'}  if self._cert else {}
    # Setup Nubank
    nu = Nubank(HttpClientWithPassword())
    nu_config = NubankConfig.from_env()
    with open(nu_config.cert_path, "rb") as f:
        nu.authenticate_with_cert(nu_config.cpf, nu_config.password, cert_data=f.read())

    # Collect transactions from Nubank
    checkings_txs = list(collect_checking_stmts(nu, after_starting_date, ynab_config.checking_account_id))
    credit_txs = list(collect_credit_stmts(nu, after_starting_date, ynab_config.credit_account_id))

    adjust_date_txs = [tx for tx in credit_txs if not before_today(tx)]
    credit_txs = [cap_date(tx) for tx in credit_txs]

    print(f'{len(checkings_txs)} checkings transactions and {len(credit_txs)} credit transactions since {ynab_config.import_date}')

    # Import transactions to YNAB
    response = ynab.transactions.create_transactions(
        ynab_config.budget_id,
        list(checkings_txs) + list(credit_txs)
    )
    print(f'{len(response.transaction_ids)} transactions imported')

    for (adj, tx) in [(adj, next(tx for tx in response.transactions if tx.import_id == adj.import_id)) for adj in adjust_date_txs]:
        print(f'Transaction date must be adjusted manually', adj, tx)

if __name__ == "__main__":
    sync(None)