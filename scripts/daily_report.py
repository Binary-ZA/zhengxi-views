# -*- coding: utf-8 -*-
"""每日全球科技行情分析报告（六板块版 v6）。

板块：
  一、纳斯达克100指数（行情 + 技术面体检 + 60日走势）
  二、制造业 vs 服务业 分化计分卡（郑希季报核心判断的日频验证）
  三、瓶颈环节温度计（他点名的产业链环节 → 代表性标的今日表现）
  四、黄金
  五、热点报告
  六、郑希视角（当日新闻溯源原话 + 两层推演 + 证伪观察点）

数据：
  腾讯行情（纳指/ETF/A股个股）、腾讯K线（纳指 800 根日K、A股 260 根）、
  新浪行情（美股科技龙头批量、伦敦金/纽约金、美元指数）、新浪资讯、本地语料。

设计原则：
  - 所有数字、表格、卡片由 Python 拼装，不消耗 token；
  - LLM 只写 5 段判断，且只能引用材料里出现过的数字；
  - 原话溯源：先用当日新闻关键词在 2012-2026 语料里做句子级检索，
    命中更早年份的表述才等于"他早就说过"，检索不到才退回最近季报。

用法:
  python scripts/daily_report.py --mode pre     # 盘前（北京 08:00）
  python scripts/daily_report.py --mode post    # 盘后（北京 20:00）
  python scripts/daily_report.py --no-llm       # 只出数据版，调试用
研究与学习辅助，非投资建议。
"""
import os, sys, json, re, argparse, glob, datetime, statistics, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.join(ROOT, "outputs")
CORPUS = os.path.join(ROOT, "references", "corpus")


# ============ 网络 ============
def _text(url, ref=None, enc="gbk", timeout=20):
    h = {"User-Agent": "Mozilla/5.0"}
    if ref:
        h["Referer"] = ref
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read().decode(enc, errors="replace")


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError(f"响应不是 JSON，前200字符：{body[:200]!r}") from None


# ============ 行情 ============
def tencent_quotes(codes):
    """腾讯行情。返回 {纯代码: {name, price, chg, time}}"""
    out = {}
    try:
        raw = _text("https://qt.gtimg.cn/q=" + ",".join(codes))
    except Exception:
        return out
    for line in raw.strip().split(";"):
        f = line.split("~")
        if len(f) > 32:
            try:
                key = f[2].lstrip(".").split(".")[0]
                out[key] = {"name": f[1], "price": float(f[3]), "chg": float(f[32]),
                            "time": f[30] if len(f) > 30 else ""}
            except ValueError:
                continue
    return out


def sina_us(symbols):
    """新浪美股批量。返回 {symbol: {name, price, chg, hi52, lo52}}"""
    out = {}
    try:
        raw = _text("https://hq.sinajs.cn/list=" + ",".join(symbols), ref="https://finance.sina.com.cn")
    except Exception:
        return out
    for line in raw.split(";"):
        m = re.match(r'\s*var hq_str_([^=]+)="(.*)"', line)
        if not m or not m.group(2).strip():
            continue
        f = m.group(2).split(",")
        if len(f) < 10:
            continue
        try:
            out[m.group(1)] = {"name": f[0], "price": float(f[1]), "chg": float(f[2]),
                               "hi52": float(f[8]), "lo52": float(f[9])}
        except ValueError:
            continue
    return out


def kline(sym, n=260):
    """日K收盘价序列。美股指数走 kline/kline，A股走 fqkline（前复权）。"""
    us = sym.startswith("us")
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={sym},day,,,{n}" if us else
           f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},qfq")
    try:
        d = json.loads(_text(url, ref="https://gu.qq.com/", enc="utf-8"))
        node = list(d["data"].values())[0]
        arr = node.get("qfqday") or node.get("day") or []
        return [{"d": x[0], "c": float(x[2])} for x in arr]
    except Exception:
        return []


def market_snapshot():
    q = tencent_quotes(["usIXIC", "usNDX", "sh513100", "sh518880"])
    snap = {"ixic": q.get("IXIC"), "ndx": q.get("NDX"),
            "etf_ndx": q.get("513100"), "etf_gold": q.get("518880"),
            "etf_ndx_hist": kline("sh513100", 5),
            "etf_gold_hist": kline("sh518880", 5),
            "ixic_hist": kline("usIXIC", 800)}

    us = sina_us(["gb_$sox", "gb_soxx"])
    snap["sox"] = us.get("gb_$sox")
    snap["soxx"] = us.get("gb_soxx")

    try:
        raw = _text("https://hq.sinajs.cn/list=hf_XAU,hf_GC,DINIW", ref="https://finance.sina.com.cn")
        m = dict(re.findall(r'hq_str_([A-Za-z0-9_]+)="([^"]*)"', raw))
        xau = (m.get("hf_XAU") or "").split(",")
        diniw = (m.get("DINIW") or "").split(",")
        snap["xau"] = float(xau[0]) if xau and xau[0] else None
        snap["usd_index"] = float(diniw[1]) if len(diniw) > 1 and diniw[1] else None
    except Exception:
        snap["xau"] = snap["usd_index"] = None
    return snap


# ============ 技术指标 ============
def _ema(vals, k):
    a = 2.0 / (k + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * a + e * (1 - a)
        out.append(e)
    return out


def _rsi(closes, period=14):
    if len(closes) < period + 2:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def tech_indicators(hist):
    """输入 kline() 的 {d,c} 列表，输出技术面指标。"""
    closes = [h["c"] for h in hist]
    n = len(closes)
    if n < 30:
        return {}
    last = closes[-1]

    def ma(k):
        return sum(closes[-k:]) / k if n >= k else None

    dif = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    dea = _ema(dif, 9)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    vol20 = statistics.stdev(rets[-20:]) * (252 ** 0.5) * 100 if len(rets) >= 20 else None

    win = closes[-250:]
    peak, mdd = win[0], 0.0
    for c in win:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1)

    return {"last": last,
            "ma5": ma(5), "ma20": ma(20), "ma60": ma(60), "ma120": ma(120),
            "rsi14": _rsi(closes, 14),
            "dif": dif[-1], "dea": dea[-1], "macd_bar": (dif[-1] - dea[-1]) * 2,
            "vol20": vol20, "mdd250": mdd * 100,
            "pct250": sum(1 for c in win if c <= last) / len(win) * 100,
            "chg5": (last / closes[-6] - 1) * 100 if n >= 6 else None,
            "chg20": (last / closes[-21] - 1) * 100 if n >= 21 else None}


def tech_verdict(t):
    """把指标翻译成一句人话，纯数据规则，不靠模型。"""
    if not t:
        return ""
    bits = []
    if t.get("ma20") and t.get("ma60"):
        last, m20, m60 = t["last"], t["ma20"], t["ma60"]
        if last > m20 > m60:
            bits.append("多头排列，站上 20 日与 60 日均线，趋势仍偏上")
        elif last < m20 and m20 > m60:
            bits.append("价格已跌破 20 日与 60 日均线，但 20 日仍在 60 日之上，均线结构尚未转空")
        elif last < m20 < m60:
            bits.append("空头排列，20 日已下穿 60 日，趋势确认转弱")
        else:
            bits.append("价格站上 20 日、但 20 日仍在 60 日之下，属于反弹修复中")
    r = t.get("rsi14")
    if r is not None:
        bits.append(f"RSI14 {r:.1f}，{'超买区' if r >= 70 else ('超卖区' if r <= 30 else '中性区')}")
    if t.get("macd_bar") is not None:
        bits.append("MACD 柱为正" if t["macd_bar"] > 0 else "MACD 柱为负")
    return "；".join(bits) + "。"


def div_verdict(m, s, gap):
    """分化判读：先看两组同向还是反向，再说谁强谁弱，避免"服务业反超"被误读成服务业在涨。"""
    if m is None or s is None:
        return "数据缺失，无法判断分化。"
    if m < 0 and s < 0:
        if abs(gap) < 0.3:
            return f"两组同步下跌，幅度接近（{m:+.2f}% vs {s:+.2f}%），分化基本消失。"
        if gap > 0:
            return f"两组同步下跌，制造业更抗跌（{m:+.2f}% vs {s:+.2f}%），分化仍在、方向没变。"
        return f"两组同步下跌，制造业跌得更多（{m:+.2f}% vs {s:+.2f}%），原有分化在收敛。"
    if m > 0 and s > 0:
        if abs(gap) < 0.3:
            return f"两组同步上涨，幅度接近（{m:+.2f}% vs {s:+.2f}%），分化基本消失。"
        return (f"两组同步上涨，{'制造业' if gap > 0 else '服务业'}更强"
                f"（{m:+.2f}% vs {s:+.2f}%），分化延续。")
    if m > 0 > s:
        return f"制造业上涨 {m:+.2f}%、服务业下跌 {s:+.2f}%，分化明确且方向未变。"
    return f"服务业上涨 {s:+.2f}%、制造业下跌 {m:+.2f}%，分化方向逆转。"


# ============ 分化计分卡 ============
# 郑希 2026 年各季报反复讲的判断：制造业属性（数据中心/AI算力/半导体）vs 服务业属性（软件/互联网/消费/金融）
MANU = [("gb_soxx", "半导体ETF SOXX"), ("gb_nvda", "英伟达"), ("gb_amd", "AMD"),
        ("gb_mu", "美光"), ("gb_avgo", "博通"), ("gb_tsm", "台积电"),
        ("gb_dell", "戴尔"), ("gb_smci", "超微电脑")]
SERV = [("gb_msft", "微软"), ("gb_goog", "谷歌"), ("gb_amzn", "亚马逊"),
        ("gb_meta", "Meta"), ("gb_orcl", "甲骨文"), ("gb_now", "ServiceNow"),
        ("gb_pltr", "PLTR"), ("gb_crwd", "CrowdStrike")]


def divergence():
    us = sina_us([s for s, _ in MANU] + [s for s, _ in SERV])

    def pack(group):
        rows, chgs = [], []
        for sym, label in group:
            d = us.get(sym)
            if not d:
                continue
            rows.append({"name": label, "chg": d["chg"]})
            chgs.append(d["chg"])
        return rows, (sum(chgs) / len(chgs) if chgs else None), sum(1 for c in chgs if c > 0), len(chgs)

    m_rows, m_avg, m_up, m_n = pack(MANU)
    s_rows, s_avg, s_up, s_n = pack(SERV)
    gap = (m_avg - s_avg) if (m_avg is not None and s_avg is not None) else None

    return {"manu": m_rows, "serv": s_rows, "manu_avg": m_avg, "serv_avg": s_avg,
            "manu_up": m_up, "manu_n": m_n, "serv_up": s_up, "serv_n": s_n,
            "gap": gap, "verdict": div_verdict(m_avg, s_avg, gap)}


# ============ 瓶颈环节温度计 ============
# 环节与"首次点名"均出自 2026 年季报/中报原文；他没点过的环节不列，避免替他发挥。
BOTTLENECK = [
    {"seg": "存储", "kind": "瓶颈", "first": "2026Q1", "codes": ["sz301308", "sz603986"], "names": "江波龙 / 兆易创新"},
    {"seg": "通信·光模块", "kind": "瓶颈", "first": "2026Q1", "codes": ["sz300308", "sz300502"], "names": "中际旭创 / 新易盛"},
    {"seg": "CPU", "kind": "瓶颈扩散", "first": "2026Q2", "codes": ["sh688041"], "names": "海光信息"},
    {"seg": "MLCC", "kind": "瓶颈扩散", "first": "2026Q2", "codes": ["sz300408", "sz000636"], "names": "三环集团 / 风华高科"},
    {"seg": "半导体设备", "kind": "景气上行", "first": "2026Q2", "codes": ["sz002371"], "names": "北方华创"},
    {"seg": "先进封装", "kind": "扩产", "first": "2026Q2", "codes": ["sh600584", "sz002156"], "names": "长电科技 / 通富微电"},
]


def bottleneck():
    q = tencent_quotes([c for b in BOTTLENECK for c in b["codes"]])
    out = []
    for b in BOTTLENECK:
        chgs = [q[c[2:]]["chg"] for c in b["codes"] if q.get(c[2:])]
        out.append({**b, "chg": sum(chgs) / len(chgs) if chgs else None})
    return out


# ============ 资讯 ============
SRC_MAP = [("cls.cn", "财联社"), ("gelonghui", "格隆汇"), ("stcn.com", "证券时报"),
           ("cs.com.cn", "中证网"), ("yicai.com", "第一财经"), ("eastmoney", "东方财富"),
           ("sina.com.cn", "新浪财经"), ("10jqka", "同花顺")]

HOT_KW = ["AI", "算力", "半导体", "芯片", "光模块", "存储", "英伟达", "台积电", "数据中心",
          "大模型", "机器人", "MLCC", "先进封装", "PCB", "CPU", "Agent", "Token"]
GOLD_KW = ["黄金", "金价", "美联储", "美元指数", "COMEX", "伦敦金", "贵金属"]


def _source(url):
    for k, v in SRC_MAP:
        if k in (url or ""):
            return v
    return "公开资讯"


def sina_roll(lid, num=50, pages=3):
    """翻页取资讯。单页只有 50 条，3 页约覆盖 2-3 天，周末才不会只剩一两条科技新闻。"""
    out = []
    for page in range(1, pages + 1):
        try:
            d = http_json(f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153"
                          f"&lid={lid}&num={num}&page={page}")
        except Exception:
            break
        items = d.get("result", {}).get("data", [])
        if not items:
            break
        for i in items:
            try:
                ts = int(i.get("ctime"))
                date = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                ts, date = 0, ""
            out.append({"title": (i.get("title") or "").strip(),
                        "intro": (i.get("intro") or "").strip(),
                        "url": i.get("url") or "",
                        "source": _source(i.get("url")),
                        "date": date, "ts": ts})
    return out


# 科技新闻太少时的兜底：只补"确实是财经/产业"的，不拿社会新闻凑数
FIN_KW = ["股", "涨", "跌", "市值", "财报", "营收", "利润", "市场", "美元", "原油", "债",
          "银行", "基金", "IPO", "并购", "产能", "订单", "出口", "关税", "央行"]


def news_pool():
    items = sina_roll(2509) + sina_roll(2516)
    seen, uniq = set(), []
    for n in items:
        if not n["title"] or n["title"] in seen:
            continue
        seen.add(n["title"])
        uniq.append(n)
    uniq.sort(key=lambda x: x["ts"], reverse=True)

    hot = [n for n in uniq if any(k in n["title"] + n["intro"] for k in HOT_KW)][:4]
    if len(hot) < 3:   # 周末/盘前科技稿少，用财经产业类补齐
        picked = {n["title"] for n in hot}
        for n in uniq:
            if n["title"] in picked:
                continue
            if any(k in n["title"] + n["intro"] for k in FIN_KW):
                hot.append(n)
                picked.add(n["title"])
            if len(hot) >= 3:
                break
        hot.sort(key=lambda x: x["ts"], reverse=True)
    gold = [n for n in uniq if any(k in n["title"] for k in GOLD_KW)][:2]
    return {"hot": hot, "gold": gold, "all": uniq}


# ============ 语料溯源 ============
TRACE_KW = ["AI", "算力", "存储", "光模块", "光通信", "半导体", "芯片", "英伟达", "台积电",
            "MLCC", "CPU", "先进封装", "PCB", "数据中心", "大模型", "Agent", "Token", "Capex",
            "机器人", "新能源", "储能", "黄金", "美联储", "美元", "通胀", "涨价", "供给", "景气"]
METHOD_WORDS = ["通胀", "供需", "瓶颈", "景气", "ROE", "周期", "涨价", "价格", "供给", "产能"]
# 命中这些"具体环节词"比命中"AI/芯片"这种泛词有说服力得多
SPECIFIC_KW = {"存储", "光模块", "光通信", "MLCC", "CPU", "先进封装", "PCB", "英伟达", "台积电",
               "Capex", "Token", "Agent", "大模型", "数据中心", "HBM", "算力"}
# 自述业绩的句子对读者没价值，直接剔除
SELF_WORDS = ["本基金", "净值", "份额", "持有人", "报告期末", "任职"]


def _sentences(para):
    return [s.strip() for s in re.split(r"(?<=[。！？；])", para) if s.strip()]


# 季报的层级标题：引用时要去掉，否则会把"产业分析：A、AI Capex产业链："带进正文
LEAD_TITLE = re.compile(
    r"^(?:产业分析|产业层面|TMT产业层面|TMT产业|分析原因|信息产业层面|新兴产业层面|"
    r"全球宏观层面|全球宏观|A股资本市场流动性层面|A股资本市场流动性|宏观经济层面|"
    r"信息产业整体|产业内部分化)\s*[:：]\s*")
LEAD_NUM = re.compile(r"^[（(]?[0-9A-Da-d一二三四五]{1,2}[）)、.．]\s*")


def _strip_lead(s):
    """剥掉句首可能叠加的多层标号（"产业分析：A、…"）。"""
    s = s.strip()
    for _ in range(3):
        before = s
        s = LEAD_TITLE.sub("", s)
        s = LEAD_NUM.sub("", s)
        if s == before:
            break
    return s


def corpus_sentences():
    """把全部语料切成句子，带出处。"""
    out = []
    for p in glob.glob(os.path.join(CORPUS, "*", "*.md")):
        try:
            text = open(p, encoding="utf-8").read()
        except OSError:
            continue
        title = (re.search(r"^#\s+(.+)$", text, re.M) or [None, os.path.basename(p)])[1].strip()
        date = (re.search(r"日期[:：]\s*([0-9\-]+)", text) or [None, ""])[1]
        kind = os.path.basename(os.path.dirname(p))
        body = text.split("---", 1)[-1]
        body = re.sub(r"^(郑希|摘自.*|查看更多投资观点)\s*$", "", body, flags=re.M)
        for para in re.split(r"\n\s*\n", body):
            # 季报里的换行是排版，不是语义分隔，先压平
            flat = re.sub(r"\s*\n+\s*", "", para.strip())
            for s in _sentences(flat):
                s = _strip_lead(s)
                if 25 <= len(s) <= 175:
                    out.append({"text": s, "date": date, "title": title, "kind": kind,
                                "year": int(date[:4]) if date[:4].isdigit() else 0})
    return out


def hot_keywords(news, n=4):
    """从当日热点标题+摘要里抽关键词，按出现次数排序。"""
    blob = " ".join(x["title"] + x["intro"] for x in news["hot"])
    score = {k: blob.count(k) for k in TRACE_KW if blob.count(k)}
    return [k for k, _ in sorted(score.items(), key=lambda kv: -kv[1])][:n]


def trace_quotes(keywords, n=2):
    """句子级溯源：优先命中具体环节词、含方法论词、且年份更早的表述。"""
    if not keywords:
        return [], []
    sents = corpus_sentences()
    newest_year = max((s["year"] for s in sents), default=2026)
    scored = []
    for s in sents:
        txt = s["text"]
        hit = [k for k in keywords if k in txt]
        if not hit or s["year"] < 2019:
            continue
        if len(txt) < 32 or any(w in txt for w in SELF_WORDS):
            continue
        score = 6 * len(hit) + 9 * sum(1 for k in hit if k in SPECIFIC_KW)
        if any(w in txt for w in METHOD_WORDS):
            score += 5
        score += min(newest_year - s["year"], 8)   # 越早说过越有说服力
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])

    picked, years = [], set()
    for _, s in scored:
        if s["year"] in years:
            continue
        years.add(s["year"])
        picked.append({"text": s["text"], "date": s["date"], "title": s["title"],
                       "kind": s["kind"], "source": s["kind"],
                       "hit": "、".join(k for k in keywords if k in s["text"])})
        if len(picked) >= n:
            break
    return picked, keywords


def fallback_quotes(n=2):
    """溯不到时退回最近季报里"瓶颈/景气"的核心句。"""
    docs = sorted(glob.glob(os.path.join(CORPUS, "定期报告", "*.md")), reverse=True)[:3]
    picked = []
    for p in docs:
        text = open(p, encoding="utf-8").read()
        date = (re.search(r"日期[:：]\s*([0-9\-]+)", text) or [None, ""])[1]
        title = (re.search(r"^#\s+(.+)$", text, re.M) or [None, ""])[1].strip()
        body = text.split("---", 1)[-1]
        for s in _sentences(body):
            s = s.strip()
            if 40 <= len(s) <= 175 and any(w in s for w in ["瓶颈", "景气", "通胀", "Capex", "供给"]):
                picked.append({"text": s, "date": date, "title": title,
                               "kind": "定期报告", "source": "定期报告", "hit": "季报原话"})
                break
        if len(picked) >= n:
            break
    return picked


# ============ LLM ============
def compose(mode, materials):
    token = os.environ.get("LLM_API_KEY", "")
    # 兼容两种填法：填到 /v1 或填完整的 /v1/chat/completions 都能用
    url = (os.environ.get("LLM_BASE_URL") or "https://api.xiaomimimo.com/v1").rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    model = os.environ.get("LLM_MODEL", "mimo-v2.6-flash")
    if not token:
        raise RuntimeError("未配置 LLM_API_KEY secret，无法调用 LLM")

    sys_prompt = (
        "你在写「每日全球科技行情分析报告」，读者是关注科技资产的个人投资者。"
        "风格：先给事实和数据，再给判断；句子短，不堆形容词，不喊口号，"
        "不用'值得注意的是''综上所述''整体来看'这类过渡句。"
        "郑希的方法只作为分析镜头，不是报告主角。\n"
        "硬性约束：\n"
        "1) 只能使用材料中出现的数字和事实，禁止编造点位、涨跌幅、公司业绩、目标价。材料没给的数字一律不写。\n"
        "2) 引用郑希原话只能来自材料给出的片段，不得改写、不得扩写、不得把推演说成他说的。\n"
        "3) 每段都要落到具体环节或具体标的，不写'市场情绪谨慎'这类空话。\n"
        "4) 禁止做单位或汇率换算（例如把'2亿美元'折成人民币），材料里没有的数字不要自己算出来。\n"
        "5) 页面已单独标注 A 股 ETF 与美股的时差，正文不要再去解释这件事。\n"
        "6) 判断价格与均线的关系只比数值大小：价格低于某条均线就写'跌破'，高于才写'站上'，"
        "不要用'逼近''接近'这类模糊说法。\n"
        "输出严格使用下面五个标记分段，不要写其它标题、不要写前言和后记：\n"
        "【纳指解读】120-200字。结合均线、RSI、MACD、回撤、波动率中至少两个具体数字，"
        "说明当前处在什么位置，以及当日涨跌的性质（趋势延续还是震荡）。\n"
        "【黄金解读】80-150字。结合金价、美元指数、当日涨跌，说明是避险驱动还是利率驱动。\n"
        "【分化判读】80-150字。对照材料里制造业组与服务业组的涨跌和上涨家数，"
        "判断'制造业强、服务业弱'这个判断今天是延续、收敛还是逆转，并指出哪几只最突出。\n"
        "【推演】250-400字，分两层（第一层…第二层…），用郑希的框架解释当前科技行情的位置。"
        "可以自然引用材料里的原话，但必须讲清这是按其方法推演，不是他本人观点。\n"
        "【观察点】恰好3条，每条一行、不超过40字、以'·'开头，必须是明天或本周能用数据验证的信号，"
        "例如某个价格、某个环节的涨跌、某个数据发布。禁止写'关注市场变化'这类无法验证的话。"
    )
    user_prompt = (
        f"日期：{datetime.date.today().strftime('%Y-%m-%d')}（{'盘前' if mode == 'pre' else '盘后'}）\n\n"
        f"【纳指与ETF行情】\n{materials['market']}\n\n"
        f"【纳指技术面】\n{materials['tech']}\n\n"
        f"【制造业属性组】\n{materials['manu']}\n\n"
        f"【服务业属性组】\n{materials['serv']}\n\n"
        f"【瓶颈环节】\n{materials['bottleneck']}\n\n"
        f"【黄金】\n{materials['gold']}\n\n"
        f"【当日热点（标题/摘要/来源）】\n{materials['hot']}\n\n"
        f"【郑希原话（可引用，带出处）】\n{materials['quotes']}"
    )
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": sys_prompt},
                     {"role": "user", "content": user_prompt}],
        "temperature": 0.5,
    }
    # MiMo 用的是新标准 max_completion_tokens，硅基流动等仍用 max_tokens
    if "xiaomimimo" in url:
        payload["max_completion_tokens"] = 3000
    else:
        payload["max_tokens"] = 3000
    data = json.dumps(payload).encode("utf-8")
    d = http_json(url, data=data,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                  timeout=180)
    u = d.get("usage") or {}
    if u:
        print(f"  模型 {model}：输入 {u.get('prompt_tokens')} + 输出 {u.get('completion_tokens')} "
              f"= {u.get('total_tokens')} tokens")
    return d["choices"][0]["message"]["content"]


def split_sections(text):
    out = {}
    for key, tag in [("nasdaq", "纳指解读"), ("gold", "黄金解读"), ("div", "分化判读"),
                     ("view", "推演"), ("watch", "观察点")]:
        m = re.search(r"【" + tag + r"】([\s\S]*?)(?=【|$)", text)
        out[key] = m.group(1).strip() if m else ""
    if not any(out.values()):
        out["view"] = text.strip()
    return out


# ============ HTML ============
def _arrow(v):
    return "▲" if (v or 0) >= 0 else "▼"


def _color(v):
    return "#d93025" if (v or 0) >= 0 else "#137333"


def _md(d):
    try:
        dt = datetime.date.fromisoformat(d)
        return f"{dt.month}/{dt.day}"
    except ValueError:
        return d


def _spark(values, w=680, h=88):
    """内联 SVG 走势图（涨红跌绿）。"""
    if len(values) < 5:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - (v - lo) / rng * h:.1f}"
                   for i, v in enumerate(values))
    color = _color(values[-1] - values[0])
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polygon points="0,{h} {pts} {w},{h}" fill="{color}" opacity=".07"/>'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def render_html(snap, tech, div, bot, news, quotes, kw, seg, mode):
    kind = "盘前" if mode == "pre" else "盘后"
    today = datetime.date.today()
    date_cn = f"{today.year}年{today.month}月{today.day}日"
    weekday = "周" + "一二三四五六日"[today.weekday()]
    ix, nd = snap.get("ixic"), snap.get("ndx")
    e_ndx, e_gold = snap.get("etf_ndx"), snap.get("etf_gold")
    sox = snap.get("sox")
    usd_txt = f"{snap['usd_index']:.2f}" if snap.get("usd_index") else "—"
    xau_txt = f"{snap['xau']:.2f}" if snap.get("xau") else "—"
    t = tech or {}

    def card(label, value, chg, sub=""):
        return (f'<div class="quote-card"><div class="q-label">{label}</div>'
                f'<div class="v">{value} <span style="color:{_color(chg)}">'
                f'{_arrow(chg)}{abs(chg or 0):.2f}%</span></div>'
                f'<div class="q-sub">{sub}</div></div>')

    def hist_line(hist):
        if len(hist) < 2:
            return ""
        parts, window = [], hist[-3:]
        for i, h in enumerate(window):
            if i == 0:
                parts.append(f"{_md(h['d'])} {h['c']:.4f}")
            else:
                prev = window[i - 1]["c"]
                parts.append(f"{_md(h['d'])} {h['c']:.4f}（{(h['c'] / prev - 1) * 100:+.2f}%）")
        return " → ".join(parts)

    ix_card = card("纳斯达克综合指数（最近收盘）",
                   f"{ix['price']:,.2f} 点" if ix else "—", ix["chg"] if ix else 0,
                   "纳斯达克 100：" + (f"{nd['price']:,.2f} 点（{nd['chg']:+.2f}%）" if nd else "—"))
    etf_card = card("纳指ETF国泰（513100）跟踪纳斯达克100",
                    f"{e_ndx['price']:.4f} 元" if e_ndx else "—", e_ndx["chg"] if e_ndx else 0,
                    hist_line(snap.get("etf_ndx_hist") or []))
    sox_sub = ""
    if sox and sox.get("hi52"):
        sox_sub = f"距 52 周高点 {(sox['price'] / sox['hi52'] - 1) * 100:+.1f}%"
    sox_card = card("费城半导体指数（SOX）", f"{sox['price']:,.2f} 点" if sox else "—",
                    sox["chg"] if sox else 0, sox_sub)

    def tv(v, fmt="{:.2f}", suffix=""):
        return (fmt.format(v) + suffix) if v is not None else "—"

    above = lambda k: "站上" if (t.get(k) and t["last"] > t[k]) else "跌破"
    rsi = t.get("rsi14")
    tech_grid = f"""<div class="grid">
<div class="cell"><div class="k">MA20</div><div class="v2">{tv(t.get('ma20'), '{:,.0f}')}</div><div class="d">{above('ma20')}</div></div>
<div class="cell"><div class="k">MA60</div><div class="v2">{tv(t.get('ma60'), '{:,.0f}')}</div><div class="d">{above('ma60')}</div></div>
<div class="cell"><div class="k">MA120</div><div class="v2">{tv(t.get('ma120'), '{:,.0f}')}</div><div class="d">{above('ma120')}</div></div>
<div class="cell"><div class="k">RSI14</div><div class="v2">{tv(rsi, '{:.1f}')}</div><div class="d">{'超买' if (rsi or 0) >= 70 else ('超卖' if rsi is not None and rsi <= 30 else '中性')}</div></div>
<div class="cell"><div class="k">MACD 柱</div><div class="v2" style="color:{_color(t.get('macd_bar'))}">{tv(t.get('macd_bar'), '{:+.1f}')}</div><div class="d">DIF {tv(t.get('dif'), '{:.0f}')} / DEA {tv(t.get('dea'), '{:.0f}')}</div></div>
<div class="cell"><div class="k">20日年化波动</div><div class="v2">{tv(t.get('vol20'), '{:.1f}', '%')}</div><div class="d">越高越不稳定</div></div>
<div class="cell"><div class="k">近250日最大回撤</div><div class="v2" style="color:#4b5563">{tv(t.get('mdd250'), '{:.1f}', '%')}</div><div class="d">从区间高点算</div></div>
<div class="cell"><div class="k">250日分位</div><div class="v2">{tv(t.get('pct250'), '{:.0f}', '%')}</div><div class="d">高于过去一年该比例交易日</div></div>
</div>"""
    verdict_html = f'<p class="verdict">{tech_verdict(t)}</p>' if tech else ""
    spark = _spark([h["c"] for h in (snap.get("ixic_hist") or [])[-60:]])

    def div_col(rows, avg, up, n, cls, label):
        inner = "".join(
            f'<div class="drow"><span>{r["name"]}</span>'
            f'<span class="dc" style="color:{_color(r["chg"])}">{r["chg"]:+.2f}%</span></div>'
            for r in rows)
        return (f'<div class="dcol {cls}"><div class="dhead"><span>{label}</span>'
                f'<span class="davg" style="color:{_color(avg)}">{avg:+.2f}%</span></div>'
                f'<div class="dsub">{up}/{n} 只上涨</div>{inner}</div>')

    div_html = ""
    if div.get("manu_avg") is not None and div.get("serv_avg") is not None:
        div_html = (f'<div class="dcards">'
                    f'{div_col(div["manu"], div["manu_avg"], div["manu_up"], div["manu_n"], "manu", "制造业属性")}'
                    f'{div_col(div["serv"], div["serv_avg"], div["serv_up"], div["serv_n"], "serv", "服务业属性")}'
                    f'</div><p class="gap">两组差 <b style="color:{_color(div["gap"])}">'
                    f'{div["gap"]:+.2f}</b> 个百分点（制造业减服务业）</p>'
                    f'<p class="verdict">{div["verdict"]}</p>')

    bot_rows = ""
    for b in bot:
        c = b.get("chg")
        chg_html = f'<span style="color:{_color(c)}">{c:+.2f}%</span>' if c is not None else '<span class="muted">—</span>'
        bot_rows += (f'<tr><td><b>{b["seg"]}</b></td><td><span class="pill">{b["kind"]}</span></td>'
                     f'<td class="muted">{b["first"]}</td><td>{b["names"]}</td>'
                     f'<td class="num">{chg_html}</td></tr>')
    bot_html = ('<table class="tbl"><thead><tr><th>环节</th><th>性质</th><th>他首次点名</th>'
                '<th>代表性标的</th><th class="num">今日</th></tr></thead><tbody>'
                f'{bot_rows}</tbody></table>')

    rows = "".join(f'<tr><td>{_md(h["d"])}</td><td class="num">{h["c"]:.4f}</td></tr>'
                   for h in (snap.get("etf_gold_hist") or [])[-3:])
    gold_table = ('<table class="tbl"><thead><tr><th>日期</th><th class="num">黄金ETF 518880</th>'
                  f'</tr></thead><tbody>{rows}</tbody></table>')

    def news_cards(items):
        out = ""
        for n in items:
            intro = n["intro"][:110] + ("…" if len(n["intro"]) > 110 else "")
            out += (f'<div class="news-card"><div class="n-title">{n["title"]}</div>'
                    f'<div class="n-intro">{intro or "（暂无摘要）"}</div>'
                    f'<div class="n-src">{n["source"]} · {n["date"]}</div></div>')
        return out or '<div class="muted">暂无</div>'

    quote_cards = "".join(
        f'<div class="q-quote"><div class="tag tag-pink">郑希原话</div>'
        f'<div class="q-src">来源：{q["title"]}（{q["date"]}）· 命中词：{q.get("hit", "")}</div>'
        f'<div class="q-text">“{q["text"]}”</div></div>' for q in quotes)

    watch_items = [x.strip().lstrip("·-• ").strip() for x in seg["watch"].split("\n") if x.strip()]
    watch_html = "".join(f"<li>{x}</li>" for x in watch_items) or '<li class="muted">（未生成）</li>'

    def paras(txt):
        return "".join(f"<p>{p}</p>" for p in txt.split("\n") if p.strip())

    kw_html = "、".join(kw) if kw else "无"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日全球科技行情分析报告 · {date_cn}</title>
<style>
:root{{--ink:#1a1a1a;--muted:#70757d;--brand:#1f5fbf;--line:#e6e8eb;--bg:#fafbfc;--card:#fff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 18px 56px}}
h1{{font-size:23px;margin:0 0 10px;letter-spacing:.5px}}
h3{{font-size:13px;color:var(--muted);font-weight:600;margin:18px 0 8px;letter-spacing:.5px}}
.meta{{color:var(--muted);font-size:12.5px;border-bottom:1px solid var(--line);padding-bottom:14px}}
h2{{font-size:17px;margin:32px 0 14px;padding-left:11px;border-left:4px solid var(--brand)}}
.quote-card{{background:#f3f6fb;border-radius:10px;padding:14px 16px;margin:10px 0}}
.q-label{{color:var(--muted);font-size:12px}}
.v{{font-size:22px;font-weight:700;margin-top:2px}}
.q-sub{{color:var(--muted);font-size:12px;margin-top:4px;line-height:1.6}}
.spark{{width:100%;height:88px;display:block;margin:14px 0 0}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}}
.cell{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:10px 11px}}
.cell .k{{font-size:11px;color:var(--muted)}}
.cell .v2{{font-size:16px;font-weight:700;margin-top:1px;font-variant-numeric:tabular-nums}}
.cell .d{{font-size:10.5px;color:var(--muted);margin-top:1px;line-height:1.4}}
.verdict{{background:#f7f8fa;border-left:3px solid #c7ccd4;padding:9px 13px;margin:12px 0;font-size:13.5px;color:#3d4045}}
.dcards{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}}
.dcol{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 13px}}
.dcol.manu{{border-top:3px solid #d93025}}
.dcol.serv{{border-top:3px solid #137333}}
.dhead{{font-size:13px;font-weight:700;display:flex;justify-content:space-between;align-items:baseline}}
.davg{{font-size:16px;font-variant-numeric:tabular-nums}}
.dsub{{font-size:11px;color:var(--muted);margin:2px 0 8px}}
.drow{{display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0;border-bottom:1px dashed #f0f2f4}}
.drow:last-child{{border-bottom:none}}
.dc{{font-variant-numeric:tabular-nums}}
.gap{{font-size:13px;margin:10px 0 0}}
.pill{{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:99px;background:#eef1f5;color:#4b5563}}
.tbl{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}}
.tbl th{{text-align:left;color:var(--muted);font-weight:500;font-size:12px;border-bottom:1px solid var(--line);padding:7px 4px}}
.tbl td{{padding:8px 4px;border-bottom:1px solid #f1f3f5}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.news-card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}}
.n-title{{font-size:14.5px;font-weight:600;line-height:1.6}}
.n-intro{{font-size:13px;color:#4b5563;margin-top:6px}}
.n-src{{font-size:11.5px;color:var(--muted);margin-top:8px}}
.q-quote{{background:#fff1f3;border-radius:10px;padding:14px 16px;margin:12px 0}}
.tag{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:99px;margin-bottom:8px}}
.tag-pink{{background:#fadde1;color:#b3283f}}
.tag-yellow{{background:#fdf3d7;color:#8a6d1a}}
.q-src{{font-size:11.5px;color:var(--muted);margin-bottom:6px}}
.q-text{{font-size:13.5px;line-height:1.85;color:#3d3d3d}}
.view-card{{background:#fffaf0;border-radius:10px;padding:16px 18px;margin:12px 0}}
.watch{{background:#f2f7ff;border-radius:10px;padding:14px 18px;margin:12px 0}}
.watch ul{{margin:6px 0 0;padding-left:18px}}
.watch li{{font-size:13.5px;line-height:1.9}}
p{{margin:8px 0;font-size:14px;line-height:1.9}}
.muted{{color:var(--muted);font-size:12px}}
.foot{{margin-top:28px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted);line-height:1.8}}
@media(max-width:520px){{.grid{{grid-template-columns:repeat(2,1fr)}}.dcards{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>每日全球科技行情分析报告</h1>
<div class="meta">日期：{date_cn}（{weekday}）｜{kind}版｜美股为最近一个已收盘交易日，A股为沪深最近交易日收盘<br>
本篇溯源关键词：{kw_html}</div>

<h2>一、纳斯达克100指数</h2>
{ix_card}
{etf_card}
<div class="muted">513100 是 A 股 ETF，收盘价反映的是前一个美股交易日的走势，与上方纳指的当日涨跌存在一天时差。</div>
{sox_card}
{spark}
<div class="muted" style="margin-top:6px">近 60 个交易日走势</div>
<h3>技术面体检</h3>
{tech_grid}
{verdict_html}
{paras(seg['nasdaq'])}

<h2>二、制造业 vs 服务业 分化计分卡</h2>
<div class="muted">郑希 2026 年各季报反复讲的判断：制造业属性的数据中心/AI算力/半导体在涨，服务业属性的软件/互联网/消费/金融在跌。这里每天用真实行情验一次。</div>
{div_html}
{paras(seg['div'])}

<h2>三、瓶颈环节温度计</h2>
<div class="muted">下面每个环节都是他在 2026 年季报/中报里亲口点过的，没点过的不列，不替他补充。</div>
{bot_html}
<div class="muted">今日＝沪深最近交易日涨跌幅；标的是该环节的代表性个股，不是他的持仓。</div>

<h2>四、黄金</h2>
{card("华安黄金ETF（518880）", f"{e_gold['price']:.4f} 元" if e_gold else "—",
      e_gold["chg"] if e_gold else 0, hist_line(snap.get("etf_gold_hist") or []))}
{gold_table}
<p class="muted">美元指数 {usd_txt} · 伦敦金 {xau_txt} 美元/盎司</p>
{paras(seg['gold'])}
{news_cards(news.get("gold") or [])}

<h2>五、热点报告</h2>
{news_cards(news.get("hot") or [])}

<h2>六、郑希视角分析</h2>
<div class="muted">原话按当日热点关键词在 2012–2026 全部公开语料里做句子级检索，优先选更早年份的表述——看的是"他早就这么说过"，不是"他今天这么说"。</div>
{quote_cards}
<div class="view-card"><div class="tag tag-yellow">按方法推演</div>{paras(seg['view'])}</div>
<div class="watch"><div class="tag" style="background:#dce8ff;color:#1f5fbf">证伪观察点</div>
<div class="muted">接下来几天用数据验证，不成立就说明推演要修</div>
<ul>{watch_html}</ul></div>

<div class="foot">仅供研究学习参考，不构成投资建议。文中"郑希原话"均来自其公开季报/访谈原文并标注出处；<br>
"按方法推演"为按其公开方法的模拟分析，非郑希本人观点。行情数据来自腾讯/新浪公开接口。</div>
</div></body></html>"""


# ============ 推送 ============
def _clip(text, n=260):
    """按句子边界截断，避免卡片摘要断在"当日性质是"中间。"""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    idx = max(cut.rfind("。"), cut.rfind("；"), cut.rfind("！"), cut.rfind("？"))
    return cut[:idx + 1] if idx >= n // 2 else cut.rstrip() + "…"


def push(seg, snap, div, report_url):
    url = os.environ.get("WEBHOOK_URL", "").strip()
    if not url:
        print("未配置 WEBHOOK_URL，跳过推送（报告已存 outputs/）")
        return
    ix = snap.get("ixic") or {}
    gold = snap.get("etf_gold") or {}
    head = (f"纳指 {ix.get('price', '—')}（{ix.get('chg', 0):+.2f}%）｜"
            f"黄金ETF {gold.get('price', '—')}（{gold.get('chg', 0):+.2f}%）")
    if div.get("gap") is not None:
        head += f"\n制造业-服务业差 {div['gap']:+.2f} 个百分点"
    excerpt = _clip(seg.get("nasdaq"), 260)
    if "open.feishu.cn" in url:
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": head}},
                    {"tag": "div", "text": {"tag": "lark_md", "content": excerpt}}]
        if report_url:
            elements.append({"tag": "action", "actions": [{
                "tag": "button", "type": "primary",
                "text": {"tag": "plain_text", "content": "查看完整报告"},
                "url": report_url}]})
        payload = {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": "每日全球科技行情 · 郑希视角"},
                       "template": "blue"},
            "elements": elements}}
    elif "oapi.dingtalk.com" in url:
        payload = {"msgtype": "text",
                   "text": {"content": f"【每日全球科技行情】\n{head}\n{excerpt}\n{report_url}"}}
    else:
        print("WEBHOOK_URL 无法识别（仅支持飞书/钉钉），跳过推送")
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("推送结果：", r.read().decode("utf-8", errors="replace")[:200])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pre", "post"], default="pre")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM，只出数据版（调试用）")
    ap.add_argument("--seg", help="从 JSON 读入五段文字并跳过 LLM（无 key 时预览用）")
    args = ap.parse_args()

    print("== 1/5 行情 ==")
    snap = market_snapshot()
    tech = tech_indicators(snap.get("ixic_hist") or [])
    print("纳指", snap.get("ixic"), "| 纳指100", snap.get("ndx"))
    print("513100", snap.get("etf_ndx"), "| 518880", snap.get("etf_gold"), "| SOX", snap.get("sox"))
    print("技术面:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tech.items()})

    print("== 2/5 分化计分卡 ==")
    div = divergence()
    f2 = lambda v: f"{v:+.2f}%" if v is not None else "—"
    print("制造业", f2(div["manu_avg"]), f"({div['manu_up']}/{div['manu_n']} 涨)",
          "| 服务业", f2(div["serv_avg"]), f"({div['serv_up']}/{div['serv_n']} 涨)",
          "| 差", (f"{div['gap']:+.2f}" if div["gap"] is not None else "—"))

    print("== 3/5 瓶颈环节 ==")
    bot = bottleneck()
    for b in bot:
        print(f"  {b['seg']:12s} {b['first']:8s} {b['chg'] if b['chg'] is None else round(b['chg'], 2)}")

    print("== 4/5 资讯 + 语料溯源 ==")
    news = news_pool()
    kw = hot_keywords(news)
    quotes, kw = trace_quotes(kw, 2)
    if not quotes:
        quotes = fallback_quotes(2)
        print("  （溯不到更早年份的原话，退回最近季报）")
    print(f"热点 {len(news['hot'])} 条 · 黄金 {len(news['gold'])} 条 · 关键词 {kw} · 原话 {len(quotes)} 段")
    for q in quotes:
        print(f"  [{q['date']}] {q['title']} → {q['text'][:70]}…")

    print("== 5/5 LLM 组稿 ==")
    market_lines = []
    for label, key in [("纳斯达克综合", "ixic"), ("纳斯达克100", "ndx"), ("纳指ETF 513100", "etf_ndx")]:
        if snap.get(key):
            market_lines.append(f"- {label}：{snap[key]['price']}（{snap[key]['chg']:+.2f}%）")
    if snap.get("sox"):
        market_lines.append(f"- 费城半导体 SOX：{snap['sox']['price']}（{snap['sox']['chg']:+.2f}%）")
    if snap.get("soxx"):
        market_lines.append(f"- SOXX 半导体ETF：{snap['soxx']['price']}（{snap['soxx']['chg']:+.2f}%）")

    tech_lines = []
    if tech:
        for k, label, fmt in [("last", "最新收盘", "{:,.2f}"), ("ma5", "MA5", "{:,.2f}"),
                              ("ma20", "MA20", "{:,.2f}"), ("ma60", "MA60", "{:,.2f}"),
                              ("ma120", "MA120", "{:,.2f}"), ("rsi14", "RSI14", "{:.1f}"),
                              ("dif", "MACD DIF", "{:+.2f}"), ("dea", "MACD DEA", "{:+.2f}"),
                              ("macd_bar", "MACD 柱", "{:+.2f}"),
                              ("vol20", "20日年化波动率", "{:.1f}%"),
                              ("mdd250", "近250日最大回撤", "{:.1f}%"),
                              ("pct250", "250日分位", "{:.0f}%"),
                              ("chg5", "近5日涨跌", "{:+.2f}%"),
                              ("chg20", "近20日涨跌", "{:+.2f}%")]:
            if tech.get(k) is not None:
                tech_lines.append(f"- {label}：{fmt.format(tech[k])}")
        tech_lines.append(f"- 均线判读：{tech_verdict(tech)}")
    if snap.get("sox") and snap["sox"].get("hi52"):
        tech_lines.append(f"- 费半距 52 周高点：{(snap['sox']['price'] / snap['sox']['hi52'] - 1) * 100:+.1f}%")

    gold_lines = []
    if snap.get("etf_gold"):
        gold_lines.append(f"- 黄金ETF 518880：{snap['etf_gold']['price']}（{snap['etf_gold']['chg']:+.2f}%）")
    if snap.get("xau"):
        gold_lines.append(f"- 伦敦金：{snap['xau']} 美元/盎司")
    if snap.get("usd_index"):
        gold_lines.append(f"- 美元指数：{snap['usd_index']}")

    materials = {
        "market": "\n".join(market_lines) or "（行情缺失）",
        "tech": "\n".join(tech_lines) or "（技术指标缺失）",
        "manu": "\n".join(f"- {r['name']}：{r['chg']:+.2f}%" for r in div["manu"])
                + (f"\n平均：{div['manu_avg']:+.2f}%，{div['manu_up']}/{div['manu_n']} 只上涨"
                   if div["manu_avg"] is not None else ""),
        "serv": "\n".join(f"- {r['name']}：{r['chg']:+.2f}%" for r in div["serv"])
                + (f"\n平均：{div['serv_avg']:+.2f}%，{div['serv_up']}/{div['serv_n']} 只上涨"
                   if div["serv_avg"] is not None else ""),
        "bottleneck": "\n".join(
            f"- {b['seg']}（{b['kind']}，{b['first']} 点名）：{b['names']} "
            + (f"{b['chg']:+.2f}%" if b["chg"] is not None else "数据缺失") for b in bot),
        "gold": "\n".join(gold_lines) or "（黄金数据缺失）",
        "hot": "\n".join(f"- {n['title']}｜{n['intro'][:80]}（{n['source']} {n['date']}）"
                         for n in news["hot"]) or "（无）",
        "quotes": "\n".join(f"- {q['title']}（{q['date']}）：“{q['text']}”" for q in quotes) or "（无）",
    }

    if args.seg:
        with open(args.seg, encoding="utf-8") as f:
            seg = json.load(f)
    elif args.no_llm:
        seg = {"nasdaq": "", "gold": "", "div": "", "watch": "",
               "view": "（--no-llm 调试模式，未调用模型）"}
    else:
        seg = split_sections(compose(args.mode, materials))

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"snap": snap, "tech": tech, "div": div, "bot": bot, "news": news,
                   "quotes": quotes, "kw": kw, "seg": seg},
                  f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(snap, tech, div, bot, news, quotes, kw, seg, args.mode))
    print("报告已写入 outputs/index.html 与 outputs/report.json")

    repo_env = os.environ.get("GITHUB_REPOSITORY", "")
    report_url = os.environ.get("REPORT_URL", "").strip()
    if not report_url and "/" in repo_env:
        owner, repo = repo_env.split("/", 1)
        report_url = f"https://{owner.lower()}.github.io/{repo}/"
    if not args.no_llm:
        push(seg, snap, div, report_url)


if __name__ == "__main__":
    main()
