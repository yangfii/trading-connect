//+------------------------------------------------------------------+
//|                                    GoldPerformanceTracker.mq5   |
//|                              Yang Fi - Gold Performance EA       |
//|                Version 2.0 - Real-time HTTP Push to Web App      |
//+------------------------------------------------------------------+
//
//  HOW THIS WORKS:
//  ───────────────
//  1. EA collects real MT5 account + trade data every N seconds
//  2. Pushes JSON directly to your web server via HTTP POST
//  3. Web dashboard receives data instantly (no file delays)
//
//  SETUP REQUIRED IN MT5:
//  ─────────────────────
//  Go to: Tools > Options > Expert Advisors
//  ✅ Check "Allow WebRequest for listed URL"
//  ➕ Add: http://127.0.0.1:5000
//  Click OK, then restart the EA
//
//+------------------------------------------------------------------+
#property copyright "Yang Fi - Gold Trader"
#property version   "2.00"
#property description "Push real MT5 data to Web Dashboard via HTTP"

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Server Connection ==="
input string   InpServerURL    = "http://127.0.0.1:5000/api/push"; // Web Server URL
input string   InpAuthToken    = "";            // Auth token (must match server AUTH_TOKEN)
input int      InpTimeout      = 3000;          // HTTP timeout (ms)

input group "=== Update Settings ==="
input int      InpUpdateSecs   = 5;             // Push interval (seconds)
input bool     InpFileBackup   = true;          // Also write JSON file (backup)
input string   InpJSONFile     = "gold_performance.json"; // Backup JSON filename

input group "=== Trade Filter ==="
input string   InpSymbol       = "";            // Symbol (empty = auto-detect from chart)
input long     InpMagic        = 0;             // Magic Number (0 = all)
input datetime InpStartDate    = 0;             // History start (0 = all)

//+------------------------------------------------------------------+
//| Performance data structure                                       |
//+------------------------------------------------------------------+
struct SPerf {
   double balance, equity, openPnL;
   int    openPositions;
   int    totalTrades, winTrades, lossTrades;
   double winRate, totalPnL, winPnL, lossPnL;
   double avgWin, avgLoss, bestTrade, worstTrade, profitFactor;
   double maxDrawdown, maxDrawdownPct, dailyDDPct, avgRR;
};

//+------------------------------------------------------------------+
//| Globals                                                          |
//+------------------------------------------------------------------+
datetime g_lastPush    = 0;
int      g_pushCount   = 0;
int      g_failCount   = 0;
bool     g_serverOK    = false;
string   g_lastStatus  = "Starting...";

//+------------------------------------------------------------------+
//| Init                                                             |
//+------------------------------------------------------------------+
int OnInit() {
   EventSetTimer(InpUpdateSecs);

   string sym      = (InpSymbol == "") ? Symbol() : InpSymbol;
   long   login    = AccountInfoInteger(ACCOUNT_LOGIN);
   string name     = AccountInfoString(ACCOUNT_NAME);
   string server   = AccountInfoString(ACCOUNT_SERVER);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
   string atype    = (AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO) ? "DEMO" : "LIVE";

   Print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
   Print("  Gold Performance Tracker v2.0  |  Yang Fi");
   Print("  ─────────────────────────────────────────");
   Print("  Account : #", login, " (", atype, ")");
   Print("  Name    : ", name);
   Print("  Server  : ", server);
   Print("  Currency: ", currency);
   Print("  Symbol  : ", sym);
   Print("  Push URL: ", InpServerURL);
   Print("  Interval: every ", InpUpdateSecs, " seconds");
   Print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
   Print("  ⚠️  Ensure MT5 WebRequest is allowed for:");
   Print("  → Tools > Options > Expert Advisors");
   Print("  → Allow WebRequest: http://127.0.0.1:5000");
   Print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");

   DoPush();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {
   EventKillTimer();
   Print("GoldPerformanceTracker stopped. Total pushes: ", g_pushCount,
         "  Failures: ", g_failCount);
}

void OnTick() {
   if(TimeCurrent() - g_lastPush >= InpUpdateSecs)
      DoPush();
}

void OnTimer() {
   DoPush();
}

//+------------------------------------------------------------------+
//| Main: calculate → build JSON → push                             |
//+------------------------------------------------------------------+
void DoPush() {
   SPerf d;
   Calculate(d);
   string json = BuildJSON(d);

   bool pushed = PushHTTP(json);

   if(InpFileBackup)
      WriteFile(json);

   g_lastPush = TimeCurrent();

   if(pushed) {
      g_pushCount++;
      if(!g_serverOK) {
         g_serverOK   = true;
         g_failCount  = 0;
         Print("✅ Server connected! Data is flowing to dashboard.");
      }
   } else {
      g_failCount++;
      if(g_failCount == 1)
         Print("❌ Push failed. Is dashboard_server.py running? Failures: ", g_failCount);
      else if(g_failCount % 10 == 0)
         Print("⚠️  Still failing. Consecutive failures: ", g_failCount);
      g_serverOK = false;
   }
}

//+------------------------------------------------------------------+
//| Calculate all metrics from real MT5 data                        |
//+------------------------------------------------------------------+
void Calculate(SPerf &d) {
   ZeroMemory(d);
   string sym = (InpSymbol == "") ? Symbol() : InpSymbol;

   // ── Live account data ──
   d.balance = AccountInfoDouble(ACCOUNT_BALANCE);
   d.equity  = AccountInfoDouble(ACCOUNT_EQUITY);

   // ── Live open positions ──
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tkt = PositionGetTicket(i);
      if(!tkt) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(InpMagic != 0 && PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      d.openPositions++;
      d.openPnL += PositionGetDouble(POSITION_PROFIT)
                 + PositionGetDouble(POSITION_SWAP);
   }

   // ── Closed trade history ──
   datetime from = (InpStartDate == 0) ? 0 : InpStartDate;
   if(!HistorySelect(from, TimeCurrent())) return;

   int    n      = HistoryDealsTotal();
   double runBal = 0, peak = 0;

   datetime dayStart = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   double   dayBal0  = 0, dayMin = 0;
   bool     dayInit  = false;

   for(int i = 0; i < n; i++) {
      ulong tkt = HistoryDealGetTicket(i);
      if(!tkt) continue;

      string dSym  = HistoryDealGetString (tkt, DEAL_SYMBOL);
      long   dMag  = HistoryDealGetInteger(tkt, DEAL_MAGIC);
      long   dEntr = HistoryDealGetInteger(tkt, DEAL_ENTRY);
      long   dType = HistoryDealGetInteger(tkt, DEAL_TYPE);

      if(sym != "" && dSym != sym) continue;
      if(InpMagic != 0 && dMag != InpMagic) continue;
      if(dEntr != DEAL_ENTRY_OUT) continue;
      if(dType != DEAL_TYPE_BUY && dType != DEAL_TYPE_SELL) continue;

      double net = HistoryDealGetDouble(tkt, DEAL_PROFIT)
                 + HistoryDealGetDouble(tkt, DEAL_SWAP)
                 + HistoryDealGetDouble(tkt, DEAL_COMMISSION);
      datetime dTime = (datetime)HistoryDealGetInteger(tkt, DEAL_TIME);

      d.totalTrades++;
      d.totalPnL += net;
      runBal     += net;

      if(d.totalTrades == 1){ d.bestTrade = net; d.worstTrade = net; }
      else {
         if(net > d.bestTrade)  d.bestTrade  = net;
         if(net < d.worstTrade) d.worstTrade = net;
      }

      if(net >= 0){ d.winTrades++;  d.winPnL  += net; }
      else        { d.lossTrades++; d.lossPnL += MathAbs(net); }

      if(runBal > peak) peak = runBal;
      double dd = runBal - peak;
      if(dd < d.maxDrawdown) {
         d.maxDrawdown = dd;
         double pa = d.balance - runBal + peak;
         if(pa > 0) d.maxDrawdownPct = (dd / pa) * 100.0;
      }

      if(dTime >= dayStart) {
         if(!dayInit){ dayBal0 = runBal - net; dayMin = runBal; dayInit = true; }
         if(runBal < dayMin) dayMin = runBal;
      }
   }

   if(d.totalTrades > 0) d.winRate      = (double)d.winTrades / d.totalTrades * 100.0;
   if(d.winTrades   > 0) d.avgWin       = d.winPnL  / d.winTrades;
   if(d.lossTrades  > 0) d.avgLoss      = d.lossPnL / d.lossTrades;
   if(d.lossPnL     > 0) d.profitFactor = d.winPnL  / d.lossPnL;
   if(d.avgLoss     > 0) d.avgRR        = d.avgWin  / d.avgLoss;

   if(dayInit && d.balance > 0) {
      double ref = d.balance - runBal + dayBal0;
      if(ref > 0) d.dailyDDPct = ((dayMin - dayBal0) / ref) * 100.0;
   }
}

//+------------------------------------------------------------------+
//| Build JSON string                                                |
//+------------------------------------------------------------------+
string BuildJSON(const SPerf &d) {
   string sym        = (InpSymbol == "") ? Symbol() : InpSymbol;
   string acctName   = AccountInfoString(ACCOUNT_NAME);
   string acctServer = AccountInfoString(ACCOUNT_SERVER);
   long   acctLogin  = AccountInfoInteger(ACCOUNT_LOGIN);
   string acctCur    = AccountInfoString(ACCOUNT_CURRENCY);
   long   leverage   = AccountInfoInteger(ACCOUNT_LEVERAGE);
   string acctType   = (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO)
                       ? "Demo" : "Live";

   string j = "{\n";
   j += "  \"source\": \"MT5_EA\",\n";
   j += "  \"updated\": \"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",\n";
   j += "  \"symbol\": \""  + sym + "\",\n";
   // ── account_number at root level for easy multi-account routing ──
   j += "  \"account_number\": " + IntegerToString(acctLogin) + ",\n";
   j += "  \"meta\": {\n";
   j += "    \"account_number\": " + IntegerToString(acctLogin) + ",\n";
   j += "    \"account_name\": \"" + acctName   + "\",\n";
   j += "    \"server\": \""       + acctServer + "\",\n";
   j += "    \"currency\": \""     + acctCur    + "\",\n";
   j += "    \"leverage\": "       + IntegerToString(leverage) + ",\n";
   j += "    \"account_type\": \"" + acctType   + "\"\n";
   j += "  },\n";
   j += "  \"account\": {\n";
   j += "    \"balance\": "  + DoubleToString(d.balance,         2) + ",\n";
   j += "    \"equity\": "   + DoubleToString(d.equity,          2) + ",\n";
   j += "    \"open_pnl\": " + DoubleToString(d.openPnL,         2) + ",\n";
   j += "    \"open_pos\": " + IntegerToString(d.openPositions)     + "\n";
   j += "  },\n";
   j += "  \"performance\": {\n";
   j += "    \"total_trades\": "  + IntegerToString(d.totalTrades)       + ",\n";
   j += "    \"win_trades\": "    + IntegerToString(d.winTrades)         + ",\n";
   j += "    \"loss_trades\": "   + IntegerToString(d.lossTrades)        + ",\n";
   j += "    \"win_rate\": "      + DoubleToString(d.winRate,      2)    + ",\n";
   j += "    \"total_pnl\": "     + DoubleToString(d.totalPnL,     2)    + ",\n";
   j += "    \"profit_factor\": " + DoubleToString(d.profitFactor, 2)    + ",\n";
   j += "    \"avg_win\": "       + DoubleToString(d.avgWin,       2)    + ",\n";
   j += "    \"avg_loss\": "      + DoubleToString(d.avgLoss,      2)    + ",\n";
   j += "    \"best_trade\": "    + DoubleToString(d.bestTrade,    2)    + ",\n";
   j += "    \"worst_trade\": "   + DoubleToString(d.worstTrade,   2)    + "\n";
   j += "  },\n";
   j += "  \"risk\": {\n";
   j += "    \"max_drawdown\": "     + DoubleToString(d.maxDrawdown,    2) + ",\n";
   j += "    \"max_drawdown_pct\": " + DoubleToString(d.maxDrawdownPct, 2) + ",\n";
   j += "    \"daily_dd_pct\": "     + DoubleToString(d.dailyDDPct,    2) + ",\n";
   j += "    \"avg_rr\": "           + DoubleToString(d.avgRR,         2) + "\n";
   j += "  }\n";
   j += "}";
   return j;
}

//+------------------------------------------------------------------+
//| HTTP POST → web server                                           |
//+------------------------------------------------------------------+
bool PushHTTP(const string &json) {
   char   body[];
   char   resp[];
   string respHdr;
   string headers = "Content-Type: application/json\r\nX-Source: MT5-EA\r\n";
   if(InpAuthToken != "")
      headers += "X-Auth-Token: " + InpAuthToken + "\r\n";

   int len = StringToCharArray(json, body, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   if(len <= 0) return false;
   ArrayResize(body, len);

   ResetLastError();
   int code = WebRequest("POST", InpServerURL, headers, InpTimeout, body, resp, respHdr);

   if(code == 200 || code == 201) return true;

   if(GetLastError() == 5203)
      Print("🔒 WebRequest blocked! Add '", InpServerURL, "' to MT5 allowed URLs.",
            "\n   Tools > Options > Expert Advisors > Allow WebRequest");
   return false;
}

//+------------------------------------------------------------------+
//| File backup (fallback)                                           |
//+------------------------------------------------------------------+
void WriteFile(const string &json) {
   int fh = FileOpen(InpJSONFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(fh == INVALID_HANDLE)
      fh = FileOpen(InpJSONFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   FileWriteString(fh, json);
   FileClose(fh);
}
//+------------------------------------------------------------------+
