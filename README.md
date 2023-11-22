# ynab-nubank-sync

Sincronize seus gastos dos seus bancos para o YNAB automaticamente (Inspirado no [br-to-ynab](https://github.com/andreroggeri/br-to-ynab))

## Configuração

Primeiro, consiga um certificado da Nubank, esse script usa o [pynubank](https://github.com/andreroggeri/pynubank), e faz uns meses que a Nubank mudou a API deles para ser mais segura, veja: https://github.com/andreroggeri/pynubank/issues/419

Depois, consiga um Token do YNAB, veja: https://api.ynab.com/#personal-access-tokens

Descubra os IDs das suas contas no YNAB, olhando as URLs. Exemplo: `https://app.ynab.com/<budget-id>/accounts/<account-id>`

```bash
$ pip install -r requirements.txt
```

## Execução

```bash
$ <somehow load env vars> python3 sync.py
```

Isso irá coletar os dados do Nubank da conta corrente e da conta de crédito e irá enviar ao YNAB.

(Eu particularmente rodo o script na GCP: https://cloud.google.com/blog/products/application-development/how-to-schedule-a-recurring-python-script-on-gcp)

## Notas

- A API da YNAB utiliza um identificador para identificar transações duplicadas, então se você rodar o script duas vezes, ele não irá duplicar as transações.
- A API da YNAB não permite criar transações com data futura, então compras parceladas serão criadas, mas com a data de hoje, necessitando uma alteração manual posteriormente.
- O script faz uso de diversas formas ad-hoc de parsear os dados do Nubank, então é bem provável que ele quebre, e exija uma alteração manual.
- O script não trata todos os eventos que vêm do feed do cartão de crédito e da conta corrente(como a API da Nubank não tem documentação, não sei quais eventos podem acontecer), então é uma boa ideia verificar se foi tudo sincronizado corretamente:
  - Tratamos eventos da conta corrente com `tags`:
    - `money-in`: entrada de dinheiro na conta
    - `money-out`: saída de dinheiro da conta
    - `payments`: pagamento de fatura do cartão de crédito
    - sem nenhuma: é feito um match no título da transação, pra verificar se é dinheiro entrando ou não
  - Tratamos eventos do cartão de crédito com `category`:
    - `transaction`:
      - sem o campo `details.charges`: compra normal
      - com o campo `details.charges`: geramos N transações, uma para cada parcela
    - `anticipate_event`: pagamento antecipado das parcelas, gerando um desconto na fatura
    - Há vários outros que podem impactar, que acabei resolvendo na mão no YNAB, mas que podem ser tratados no script(IOF, compras internacionais, etc)