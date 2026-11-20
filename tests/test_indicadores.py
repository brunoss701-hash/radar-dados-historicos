import unittest
import datetime as dt
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import coletor

class TesteIndicadores(unittest.TestCase):
    def test_ema_constante(self):
        self.assertAlmostEqual(coletor.ema([100]*250,200),100)
    def test_rsi_subida(self):
        self.assertAlmostEqual(coletor.rsi(list(range(1,40))),100)
    def test_atr_constante(self):
        rows=[(i*14400,100,102,98,100,10) for i in range(100)]
        self.assertAlmostEqual(coletor.atr(rows),4)
    def test_macd_constante(self):
        m,s,h=coletor.macd([100]*250)
        self.assertAlmostEqual(m,0)
        self.assertAlmostEqual(s,0)
        self.assertAlmostEqual(h,0)
    def test_indicadores_200_barras(self):
        rows=[(i*14400,100+i/10,102+i/10,99+i/10,101+i/10,1000+i) for i in range(240)]
        i=coletor.indicators(rows)
        self.assertIsNotNone(i['ema200']);self.assertIsNotNone(i['rsi14'])
    def test_filtra_candle_incompleto(self):
        now=coletor.NOW_TS
        rows=[(now-14400,1,2,1,1.5,20),(now-200,1,2,1,1.5,20)]
        self.assertEqual(len(coletor.normalize(rows,14400)),1)
if __name__ == '__main__':unittest.main()

class TesteTriagem(unittest.TestCase):
    def test_triagem_com_barras_ficticias(self):
        from unittest.mock import patch
        import datetime as dt
        base = (coletor.NOW_TS // 86400 - 400) * 86400
        daily=[(base+i*86400, 100+i*.2, 102+i*.2, 99+i*.2, 101+i*.2, 1000+i) for i in range(350)]
        base4=(coletor.NOW_TS // 14400 - 400) * 14400
        four=[(base4+i*14400, 100+i*.2, 102+i*.2, 99+i*.2, 101+i*.2, 1000+i) for i in range(350)]
        with patch.object(coletor, 'binance_snapshot', return_value=(2,[{'base':'BTC','pair':'BTCUSDT','change_24h_pct':2,'quote_volume_24h_usdt':50000000,'last_price_usdt':120}],None)), \
             patch.object(coletor, 'crypto_history', return_value=({'1d':daily,'4h':four},'Binance','https://example.com')), \
             patch.object(coletor, 'stock_history', return_value=({'1d':daily,'1h':four},'https://example.com')):
            result=coletor.scan(1,2)
        self.assertEqual(result['counts']['crypto_with_ohlcv'],1)
        self.assertEqual(result['counts']['equities_etfs_with_ohlcv'],2)
        self.assertEqual(len(result['candidates']),3)
        self.assertTrue(all(c['screening_only'] for c in result['candidates']))
