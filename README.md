# Coletor de dados para o Radar Integrado (Quantfury)

Projeto pronto para publicar em **repositório público** do GitHub. O GitHub Actions tenta coletar dados históricos de criptomoedas (Binance, Bybit, Kraken, Coinbase) e ações/ETFs (Yahoo e Stooq), calcula indicadores e disponibiliza `data/latest.json` para leitura pelo ChatGPT.

**Nenhuma chave de API, senha de corretora ou acesso à conta Quantfury é necessário.** O sistema NÃO executa ordens. As cotações externas são indicativas, não preços executáveis da Quantfury. Os dados são acessíveis ao público se o repositório for público. Não armazene credenciais nesse repositório.

## Instalação (GitHub)

1. Crie um repositório **público** novo, por exemplo `radar-market-data`, na sua conta GitHub.
2. Coloque os arquivos deste pacote no repositório, preservando `.github/workflows/coleta.yml`. No GitHub, é possível enviar arquivos em lote pelo navegador; para diretórios ocultos, o caminho deve ser preservado. Alternativamente, descompacte e faça `git add . && git commit -m "Coletor" && git push` em um clone.
3. Abra **Actions > Coleta de dados históricos OHLCV > Run workflow**. Autorize Actions na configuração do repositório se estiver desativado.
4. Confirme que a execução concluiu e criou `data/latest.json`. Se deu erro, abra os logs da Action, que indicam fontes inacessíveis. Se necessário ajuste as permissões em **Settings > Actions > General > Workflow permissions > Read and write permissions** (a configuração `permissions: contents: write` já é solicitada pelo workflow, mas políticas da conta podem restringir).
5. O link final terá este formato: `https://raw.githubusercontent.com/SEU_USUARIO/radar-market-data/main/data/latest.json` (substitua por URL real de seu repositório/branch).
6. Envie-me o link **real** para eu atualizar o Radar Integrado para consultá-lo em toda execução. Sem o link publicado, o radar ainda não consegue ler os dados coletados.

O agendamento está definido para uma execução por hora, aproximadamente no minuto 11. **GitHub Actions não garante execução no minuto exato**. A automação do ChatGPT deve conferir `generated_at_utc` e o horário do último candle e declarar dados vencidos/insuficientes quando necessário.

## Fontes, validação e limites

- Binance mercado spot, com `exchangeInfo`, `ticker/24hr`, `klines` 4h/1d. A triagem inicial usa o universo de pares USDT realmente retornados. Os limites de processamento histórico estão em `CRYPTO_LIMIT` e `EQUITIES_LIMIT` (workflow). A existência na Binance não prova listagem na Quantfury.
- Fallback cripto **em uma mesma corretora por par**: Bybit 4h/1d, Kraken X/USD 4h/1d, Coinbase X/USD 1d + 1h agregado em 4h UTC. Pares USD são rotulados como USD, distintos de USDT.
- Ações/ETFs: Yahoo 1d/1h, Stooq apenas 1d como fallback. Não é criada uma vela 4h artificial para o mercado acionário.
- Usa somente candles fechados, remove candles incompletos, inválidos e duplicados, e calcula EMA20/50/200, RSI14 (Wilder), MACD12/26/9, ATR14 (Wilder), volume relativo das últimas 20 velas completas e extremos de 20 velas. `null` significa que a série disponível é insuficiente.
- `screening_priority` ordena **prioridade de estudo** e não é um sinal executável, backtest ou recomendação de compra.
- O coletor usa endpoints públicos sem garantia de SLA. Região, bloqueios, limites, URLs ou termos de uso podem impedir algum fornecedor. A Action falha sem atualizar arquivo se nenhum ativo tiver candles confiáveis.
- Fonte diária da Binance via arquivos ZIP também está disponível no projeto público oficial `https://github.com/binance/binance-public-data`, com publicação do dia anterior; não substitui candles atuais intradiários.

## Teste local (opcional)

Com Python 3.11+ instalado e conexão normal à internet:

```bash
python coletor.py
```

Serão criados indicadores e contadores em `data/latest.json`. É possível aumentar o universo elevando limites de processamento no workflow, respeitando cota e duração da execução.

## Importante

Esta solução separa *coleta de dados* (no GitHub Actions, com rede HTTP normal) de *leitura pelo ChatGPT* (através do arquivo público de texto no `raw.githubusercontent.com`). O acesso a endpoints históricos é bloqueado no navegador de busca atual, mas o arquivo público de texto do GitHub foi testado como legível. Ainda é necessário publicar o repositório e verificar uma execução real do coletor no GitHub: sem isso, o acesso histórico contínuo NÃO está resolvido em produção.
