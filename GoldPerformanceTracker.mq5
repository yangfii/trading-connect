//+------------------------------------------------------------------+
//|                                    GoldPerformanceTracker.mq5   |
//|                              Yang Fi - Gold Performance EA       |
//|       Version 2.1 - Push + Remote Position Close from Web App   |
//+------------------------------------------------------------------+
//
//  HOW THIS WORKS:
//  ───────────────
//  1. EA collects real MT5 account + trade data every N seconds
//  2. Pushes JSON (incl. open positions) to your web server via HTTP POST
//  3. EA polls server for queued commands (close / close_all) and executes
//  4. EA reports each command's outcome back to the server
//  5. Web dashboard sees live updates via SSE
//
//  SETUP REQUIRED IN MT5:
//  ─────────────────────
//  Go to: Tools > Options > Expert Advisors
//  ✅ Check "Allow WebRequest for listed URL"
//  ➕ Add: http://127.0.0.1:5000   (for local dev)
//  ➕ Add: https://trader-performance.astracambodia.com   (for production)
//  Click OK, then restart the EA
//
//+------------------------------------------------------------------+
#property copyright "Yang Fi - Gold Trader"
#property version   "2.10"
#property description "Push real MT5 data to Web Dashboard via HTTP + remote close"

#include <Trade\Trade.mqh>
CTrade g_trade;

//+------------------------------------------------------------------+
//| Inputs                                                           |
//+------------------------------------------------------------------+
input group "=== Server Connection ==="
input string   InpServerURL    = "http://127.0.0.1:5000/api/push"; // Web Server URL (push endpoint)
input string   InpServerBase   = "http://127.0.0.1:5000";          // Web Server base URL (for commands)
input string   InpAuthToken    = "";            // Auth token (must match server AUTH_TOKEN)
input string   InpAccountGroup = "";            // Group label: Prop / Personal / Demo / Live (optional)
input int      InpTimeout      = 3000;          // HTTP timeout (ms)

input group "=== Update Settings ==="
input int      InpUpdateSecs   = 5;             // Push interval (seconds)
input bool     InpFileBackup   = true;          // Also write JSON file (backup)
input string   InpJSONFile     = "gold_performance.json"; // Backup JSON filename

input group "=== Remote Trading ==="
input bool     InpAllowRemoteClose = true;      // Allow web dashboard to close positions
input int      InpCloseSlippage    = 20;        // Slippage in points for close orders

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
//| Open position snapshot (for JSON)                                |
//+------------------------------------------------------------------+
struct SPos {
   ulong    ticket;
   string   symbol;
   int      type;          // 0=BUY, 1=SELL
   double   volume;
   double   priceOpen;
   double   priceCurrent;
   double   sl, tp;
   double   profit;
   double   swap;
   datetime timeOpen;
   long     magic;
   string   comment;
};
SPos g_positions[];

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
   Print("  Gold Performance Tracker v2.1  |  Yang Fi");
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
   // Pull pending commands from server BEFORE building snapshot,
   // so the snapshot we push afterwards reflects post-execution state.
   if(InpAllowRemoteClose)
      FetchAndExecuteCommands();

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
   ArrayResize(g_positions, 0);
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tkt = PositionGetTicket(i);
      if(!tkt) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(InpMagic != 0 && PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      d.openPositions++;
      d.openPnL += PositionGetDouble(POSITION_PROFIT)
                 + PositionGetDouble(POSITION_SWAP);

      int idx = ArraySize(g_positions);
      ArrayResize(g_positions, idx + 1);
      g_positions[idx].ticket       = tkt;
      g_positions[idx].symbol       = PositionGetString (POSITION_SYMBOL);
      g_positions[idx].type         = (int)PositionGetInteger(POSITION_TYPE);
      g_positions[idx].volume       = PositionGetDouble (POSITION_VOLUME);
      g_positions[idx].priceOpen    = PositionGetDouble (POSITION_PRICE_OPEN);
      g_positions[idx].priceCurrent = PositionGetDouble (POSITION_PRICE_CURRENT);
      g_positions[idx].sl           = PositionGetDouble (POSITION_SL);
      g_positions[idx].tp           = PositionGetDouble (POSITION_TP);
      g_positions[idx].profit       = PositionGetDouble (POSITION_PROFIT);
      g_positions[idx].swap         = PositionGetDouble (POSITION_SWAP);
      g_positions[idx].timeOpen     = (datetime)PositionGetInteger(POSITION_TIME);
      g_positions[idx].magic        = PositionGetInteger(POSITION_MAGIC);
      g_positions[idx].comment      = PositionGetString (POSITION_COMMENT);
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
   j += "    \"account_type\": \"" + acctType   + "\",\n";
   j += "    \"group\": \""        + InpAccountGroup + "\"\n";
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
   j += "  },\n";
   j += "  \"positions\": [";
   int np = ArraySize(g_positions);
   for(int i = 0; i < np; i++) {
      if(i > 0) j += ",";
      j += "\n    {";
      j += "\"ticket\":"        + IntegerToString((long)g_positions[i].ticket);
      j += ",\"symbol\":\""     + JSONEscape(g_positions[i].symbol) + "\"";
      j += ",\"type\":"         + IntegerToString(g_positions[i].type);
      j += ",\"type_str\":\""   + (g_positions[i].type == 0 ? "BUY" : "SELL") + "\"";
      j += ",\"volume\":"       + DoubleToString(g_positions[i].volume,       2);
      j += ",\"price_open\":"   + DoubleToString(g_positions[i].priceOpen,    5);
      j += ",\"price_current\":"+ DoubleToString(g_positions[i].priceCurrent, 5);
      j += ",\"sl\":"           + DoubleToString(g_positions[i].sl,           5);
      j += ",\"tp\":"           + DoubleToString(g_positions[i].tp,           5);
      j += ",\"profit\":"       + DoubleToString(g_positions[i].profit,       2);
      j += ",\"swap\":"         + DoubleToString(g_positions[i].swap,         2);
      j += ",\"time_open\":\""  + TimeToString(g_positions[i].timeOpen, TIME_DATE|TIME_MINUTES) + "\"";
      j += ",\"magic\":"        + IntegerToString(g_positions[i].magic);
      j += ",\"comment\":\""    + JSONEscape(g_positions[i].comment) + "\"";
      j += "}";
   }
   if(np > 0) j += "\n  ";
   j += "]\n";
   j += "}";
   return j;
}

//+------------------------------------------------------------------+
//| Minimal JSON string escape                                       |
//+------------------------------------------------------------------+
string JSONEscape(const string s) {
   string r = s;
   StringReplace(r, "\\", "\\\\");
   StringReplace(r, "\"", "\\\"");
   StringReplace(r, "\n", " ");
   StringReplace(r, "\r", " ");
   StringReplace(r, "\t", " ");
   return r;
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
//| Build commands base URL                                          |
//+------------------------------------------------------------------+
string CommandsURL() {
   long acct = AccountInfoInteger(ACCOUNT_LOGIN);
   string base = InpServerBase;
   // Trim trailing slash
   if(StringLen(base) > 0 && StringSubstr(base, StringLen(base)-1, 1) == "/")
      base = StringSubstr(base, 0, StringLen(base)-1);
   return base + "/api/commands?account=" + IntegerToString(acct);
}

string ResultURL() {
   string base = InpServerBase;
   if(StringLen(base) > 0 && StringSubstr(base, StringLen(base)-1, 1) == "/")
      base = StringSubstr(base, 0, StringLen(base)-1);
   return base + "/api/command_result";
}

//+------------------------------------------------------------------+
//| Fetch pending commands and execute                               |
//+------------------------------------------------------------------+
void FetchAndExecuteCommands() {
   string url = CommandsURL();
   string headers = "X-Source: MT5-EA\r\n";
   if(InpAuthToken != "")
      headers += "X-Auth-Token: " + InpAuthToken + "\r\n";

   char   body[];
   char   resp[];
   string respHdr;

   ResetLastError();
   int code = WebRequest("GET", url, headers, InpTimeout, body, resp, respHdr);
   if(code != 200) {
      if(GetLastError() == 5203)
         Print("🔒 WebRequest blocked for commands URL. Add base URL to allowed URLs.");
      return;
   }

   string json = CharArrayToString(resp, 0, WHOLE_ARRAY, CP_UTF8);
   // Expect: {"commands":[{"id":"...","action":"close","ticket":12345}, ...]}
   ProcessCommandsJSON(json);
}

//+------------------------------------------------------------------+
//| Naive JSON walker — extracts each command object and dispatches  |
//+------------------------------------------------------------------+
void ProcessCommandsJSON(const string json) {
   int p = StringFind(json, "\"commands\"");
   if(p < 0) return;
   int arrStart = StringFind(json, "[", p);
   int arrEnd   = StringFind(json, "]", arrStart);
   if(arrStart < 0 || arrEnd < 0) return;

   int depth = 0;
   int objStart = -1;
   for(int i = arrStart + 1; i < arrEnd; i++) {
      string c = StringSubstr(json, i, 1);
      if(c == "{") {
         if(depth == 0) objStart = i;
         depth++;
      } else if(c == "}") {
         depth--;
         if(depth == 0 && objStart >= 0) {
            string obj = StringSubstr(json, objStart, i - objStart + 1);
            HandleCommand(obj);
            objStart = -1;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Extract a string field from a flat JSON object                  |
//+------------------------------------------------------------------+
string JSONField(const string obj, const string key) {
   string needle = "\"" + key + "\"";
   int p = StringFind(obj, needle);
   if(p < 0) return "";
   int colon = StringFind(obj, ":", p);
   if(colon < 0) return "";
   int i = colon + 1;
   // skip whitespace
   while(i < StringLen(obj)) {
      string c = StringSubstr(obj, i, 1);
      if(c != " " && c != "\t" && c != "\n" && c != "\r") break;
      i++;
   }
   if(i >= StringLen(obj)) return "";
   string first = StringSubstr(obj, i, 1);
   if(first == "\"") {
      // quoted string
      int end = StringFind(obj, "\"", i + 1);
      if(end < 0) return "";
      return StringSubstr(obj, i + 1, end - i - 1);
   }
   // numeric / literal — read until comma, brace, or whitespace
   int end = i;
   while(end < StringLen(obj)) {
      string c = StringSubstr(obj, end, 1);
      if(c == "," || c == "}" || c == " " || c == "\n" || c == "\r" || c == "\t") break;
      end++;
   }
   return StringSubstr(obj, i, end - i);
}

//+------------------------------------------------------------------+
//| Dispatch one command                                             |
//+------------------------------------------------------------------+
void HandleCommand(const string obj) {
   string id     = JSONField(obj, "id");
   string action = JSONField(obj, "action");
   string ticketStr = JSONField(obj, "ticket");
   ulong  ticket = (ulong)StringToInteger(ticketStr);

   bool   ok      = false;
   string message = "";

   if(action == "close") {
      ok = ClosePositionByTicket(ticket, message);
   }
   else if(action == "close_all") {
      ok = CloseAllPositions(message);
   }
   else {
      message = "unknown action: " + action;
   }

   ReportResult(id, action, ticket, ok, message);
}

//+------------------------------------------------------------------+
//| Close a single position by ticket                                |
//+------------------------------------------------------------------+
bool ClosePositionByTicket(const ulong ticket, string &message) {
   if(!PositionSelectByTicket(ticket)) {
      message = "position not found (already closed?)";
      return false;
   }

   g_trade.SetDeviationInPoints((ulong)InpCloseSlippage);
   g_trade.SetTypeFillingBySymbol(PositionGetString(POSITION_SYMBOL));

   bool ok = g_trade.PositionClose(ticket, (ulong)InpCloseSlippage);
   if(ok) {
      message = "closed (retcode=" + IntegerToString(g_trade.ResultRetcode())
              + ", deal=" + IntegerToString((long)g_trade.ResultDeal()) + ")";
      Print("✅ Remote close succeeded: ticket=", ticket, " ", message);
   } else {
      message = "close failed retcode=" + IntegerToString(g_trade.ResultRetcode())
              + " " + g_trade.ResultRetcodeDescription();
      Print("❌ Remote close FAILED: ticket=", ticket, " ", message);
   }
   return ok;
}

//+------------------------------------------------------------------+
//| Close all positions (filtered by symbol/magic if configured)     |
//+------------------------------------------------------------------+
bool CloseAllPositions(string &message) {
   string sym = (InpSymbol == "") ? "" : InpSymbol;
   int closed = 0, failed = 0;

   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tkt = PositionGetTicket(i);
      if(!tkt) continue;
      if(sym != "" && PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(InpMagic != 0 && PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;

      string msg;
      if(ClosePositionByTicket(tkt, msg)) closed++;
      else failed++;
   }
   message = "closed=" + IntegerToString(closed) + " failed=" + IntegerToString(failed);
   return failed == 0;
}

//+------------------------------------------------------------------+
//| Report command result back to server                             |
//+------------------------------------------------------------------+
void ReportResult(const string id, const string action,
                  const ulong ticket, const bool ok, const string message) {
   long acct = AccountInfoInteger(ACCOUNT_LOGIN);

   string j = "{";
   j += "\"id\":\""        + id      + "\"";
   j += ",\"account\":"    + IntegerToString(acct);
   j += ",\"action\":\""   + action  + "\"";
   j += ",\"ticket\":"     + IntegerToString((long)ticket);
   j += ",\"ok\":"         + (ok ? "true" : "false");
   j += ",\"message\":\""  + JSONEscape(message) + "\"";
   j += ",\"reported_at\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\"";
   j += "}";

   string headers = "Content-Type: application/json\r\nX-Source: MT5-EA\r\n";
   if(InpAuthToken != "")
      headers += "X-Auth-Token: " + InpAuthToken + "\r\n";

   char   body[];
   char   resp[];
   string respHdr;
   int len = StringToCharArray(j, body, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   if(len <= 0) return;
   ArrayResize(body, len);

   ResetLastError();
   WebRequest("POST", ResultURL(), headers, InpTimeout, body, resp, respHdr);
   // Best-effort — failures here are non-fatal.
}
//+------------------------------------------------------------------+
