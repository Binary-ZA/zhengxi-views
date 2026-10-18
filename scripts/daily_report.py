# -*- coding: utf-8 -*-
"""GitHub Actions 每日日报编排脚本：真正调用本 skill 的脚本与语料。

流程:
  1. fetch_fund_data.parse_pingzhong/parse_holdings 抓 001513 真实净值与最新季度持仓（天天基金公开数据）
  2. search_corpus.py 在郑希语料中检索板块关键词，拿到带出处的原文片段
  3. （可选）新浪财经滚动新闻 API 抓要闻，失败不影响主流程
  4. 调 GitHub Models（用 Actions 自带 GITHUB_TOKEN，免费）组稿
  5. 输出 outputs/report.md；若配置了 WEBHOOK_URL secret 则推送到飞书/钉钉群机器人

用法:
  python scripts/daily_report.py --mode pre    # 盘前（北京 08:00）
  python scripts/daily_report.py --mode post   # 盘后（北京 20:00）
研究与学习辅助，非投资建议。
"""
import os, sys, json, re, argparse, subprocess, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fetch_fund_data as F  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = os.path.join(ROOT, "outputs")
KEYWORDS = ["光通信", "AI算力", "算力", "半导体", "新能源", "电力"]


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- 1. 基金真实数据 ----------
def fund_block():
    pz = F.parse_pingzhong("001513")
    if not pz:
        return "（001513 净值数据抓取失败）"
    trend = pz.get("单位净值走势") or []
    lines = []
    if trend:
        last, prev = trend[-1], trend[-2] if len(trend) > 1 else None
        nav_date = F.fmt_ts(last["x"])
        chg = last.get("equityReturn")
        lines.append(f"{pz.get('fS_name')}（001513）最新净值：{last['y']}（{nav_date}，日涨跌 {chg}%）")
    ac = pz.get("累计净值走势") or []
    if ac:
        for label, days in [("近1周", 7), ("近1月", 30), ("近3月", 91)]:
            r = F.window_return(ac, days)
            if r is not None:
                lines.append(f"{label}收益：{r}%")
        y = F.year_return(ac)
        if y is not None:
            lines.append(f"今年以来：{y}%")
    year = datetime.date.today().year
    try:
        quarters = F.parse_holdings("001513", year) or F.parse_holdings("001513", year - 1)
        if quarters:
            q = quarters[0]
            tops = "、".join(f"{h['股票名称']}({h['占净值比']})" for h in q.get("holdings", [])[:10])
            lines.append(f"最新季度持仓（{q['year']}年Q{q['quarter']}）：{tops}")
    except Exception as e:
        lines.append(f"（持仓抓取失败：{e}）")
    return "\n".join(lines)


# ---------- 2. 语料检索（skill 核心）----------
def corpus_block():
    cmd = [sys.executable, os.path.join(HERE, "search_corpus.py"), *KEYWORDS, "--any", "--max", "10"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, cwd=ROOT)
        text = r.stdout.decode("utf-8", errors="replace").strip()
        return text if text else "（语料无命中）"
    except Exception as e:
        return f"（语料检索失败：{e}）"


# ---------- 3. 要闻（尽力而为）----------
def news_block():
    try:
        url = "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=20&page=1"
        d = http_json(url)
        items = d.get("result", {}).get("data", [])[:12]
        lines = [f"- {i.get('title','')}（{i.get('ctime','')}）" for i in items]
        return "\n".join(lines) if lines else "（无要闻返回）"
    except Exception as e:
        return f"（要闻抓取失败：{e}；请基于基金数据与语料生成，不要编造新闻）"


# ---------- 4. LLM 组稿（GitHub Models，用 Actions 自带 token）----------
def compose(mode, materials):
    token = os.environ.get("GITHUB_TOKEN", "")
    today = datetime.date.today().strftime("%Y-%m-%d")
    kind = "盘前简报" if mode == "pre" else "盘后复盘"
    structure = ("①隔夜/盘前要闻 ②科技板块景气度点评 ③001513净值近况 ④今日关注点"
                 if mode == "pre" else
                 "①今日行情速览 ②重仓股表现对照 ③复盘点评 ④明日留意点")
    sys_prompt = (
        "你是研究助理，用易方达基金经理郑希公开表达过的投资方法做市场分析。"
        "框架要点：①景气投资，找'通胀'方向，偏爱供给端创造需求的科技型通胀；"
        "②全球视野+中国比较优势（如光通信）；③偏好ROE从低到高的弹性标的；"
        "④高流动性优先；⑤高换手、周期拼接；⑥强调客观。"
        "硬性要求：语料片段中给出的郑希原话可引用并标注出处（类型|日期|标题）；"
        "语料没有的不要杜撰成他的话，一律写'按其方法推演'。"
        "只使用材料中出现的数据，禁止编造净值、涨跌幅等数字。"
        f"输出500-800字简明分点的「郑希视角·{kind}」，结构：{structure}。"
        "末尾固定加一行：⚠️ 仅供研究学习参考，不构成投资建议。文中'郑希框架分析'为模拟推演，非郑希本人观点。"
    )
    user_prompt = f"日期：{today}（若为周末/休市日请输出休市说明+近期梳理，不虚构行情）\n\n【基金真实数据】\n{materials['fund']}\n\n【语料原文片段（可引用，带出处）】\n{materials['corpus']}\n\n【财经要闻列表】\n{materials['news']}"
    data = json.dumps({
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
        "max_tokens": 2000,
        "temperature": 0.4,
    }).encode("utf-8")
    d = http_json("https://models.github.ai/inference/chat/completions", data=data,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                  timeout=120)
    return d["choices"][0]["message"]["content"]


# ---------- 5. 推送（飞书/钉钉群机器人 webhook）----------
def push(report):
    url = os.environ.get("WEBHOOK_URL", "").strip()
    if not url:
        print("未配置 WEBHOOK_URL，跳过推送（报告已存 outputs/report.md）")
        return
    report = "【郑希视角日报】\n" + report  # 固定标题，保证命中飞书/钉钉的关键词安全校验
    if "open.feishu.cn" in url:
        payload = {"msg_type": "text", "content": {"text": report}}
    elif "oapi.dingtalk.com" in url:
        payload = {"msgtype": "text", "text": {"content": report}}
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
    args = ap.parse_args()

    print("== 1/4 抓基金真实数据 ==")
    fund = fund_block()
    print(fund[:300])
    print("== 2/4 检索郑希语料 ==")
    corpus = corpus_block()
    print(f"语料命中 {len(corpus)} 字符")
    print("== 3/4 抓财经要闻 ==")
    news = news_block()
    print("== 4/4 LLM 组稿 ==")
    report = compose(args.mode, {"fund": fund, "corpus": corpus, "news": news})

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print("报告已写入", out)
    push(report)


if __name__ == "__main__":
    main()
