#!/usr/bin/env python3
"""Fill the generated parts of the site from the LaTeX CV.

The CV (cv_attia_full.tex) is the single source for every list on the site:
publications, talks, teaching, supervision, outreach, positions, education,
awards, and the one-line code entries. Each page marks a generated region as

    <!-- BEGIN name -->
    ...
    <!-- END name -->

and this script replaces what sits between the two markers. Everything outside
the markers is hand-written and left untouched. The shared header and footer
are generated the same way, so the navigation stays identical on every page.

Usage:
    python3 tools/build_lists.py --cv /path/to/cv_attia_full.tex
    CV_TEX=/path/to/cv_attia_full.tex python3 tools/build_lists.py
    python3 tools/build_lists.py --cv ... --check   # report, write nothing
"""

import argparse
import datetime as dt
import html
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent

PAGES = [
    ("index.html", "Home"),
    ("research.html", "Research"),
    ("codes.html", "Codes"),
    ("papers.html", "Papers"),
    ("talks.html", "Talks"),
    ("teaching.html", "Teaching"),
]

MONTHS = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]

# ADS bibstems for the journals that appear in the CV. A journal missing
# here makes the build fail loudly rather than emit a broken link.
BIBSTEMS = {
    "Astronomy & Astrophysics": "A&A",
    "Monthly Notices of the Royal Astronomical Society": "MNRAS",
    "The Astrophysical Journal Letters": "ApJ",  # letters carry the L qualifier, not their own stem
    "The Astrophysical Journal": "ApJ",
    "The Planetary Science Journal": "PSJ",
}

# Positions left off the home page (they stay on the CV and in the PDF). The 2024–2025
# Geneva postdoc was a transition between PhD and fellowship (Mara, 23 October 2026).
HOME_POSITIONS_SKIP = ("Geneva Observatory",)

# The CV speaks of Mara in the third person; the site is in the first person.
VOICE = [
    (r"\bwith Mara as\b", "with me as"),
    (r"\bwhich Mara is\b", "which I am"),
    (r"\bMara is\b", "I am"),
]


# ---------------------------------------------------------------- LaTeX to HTML

def _group(s, i):
    """Return (content, index after the closing brace) for a {...} at s[i]."""
    if i >= len(s) or s[i] != "{":
        raise ValueError(f"expected '{{' at {i} in {s[:80]!r}")
    depth, j = 0, i
    while j < len(s):
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError(f"unbalanced braces in {s[:80]!r}")


def _text(s):
    """Plain-text substitutions on a run that holds no commands."""
    s = s.replace("``", "\u201c").replace("''", "\u201d")
    s = s.replace("---", "\u2014").replace("--", "\u2013")
    s = s.replace("~", "\u00a0")
    return html.escape(s, quote=False)


def tex2html(s, keep_bold=False):
    """Convert the small LaTeX subset used in the CV to HTML."""
    out, i, run = [], 0, []

    def flush():
        if run:
            out.append(_text("".join(run)))
            run.clear()

    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)\s*", s[i:])
            if not m:  # escaped symbol: \& \% \, \_
                sym = s[i + 1]
                i += 2
                if sym == ",":
                    run.append("\u2009")
                elif sym in "&%_#$":
                    run.append(sym)
                else:
                    run.append(sym)
                continue
            name = m.group(1)
            i += m.end()
            flush()
            if name == "href":
                url, i = _group(s, i)
                label, i = _group(s, i)
                url = url.replace("\\%", "%").replace("\\#", "#")
                url = quote(url, safe=":/?#[]@!$&'()*+,;=%-._~")  # percent-encode non-ASCII
                out.append(f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{tex2html(label, keep_bold)}</a>')
            elif name == "textbf":
                body, i = _group(s, i)
                inner = tex2html(body, keep_bold)
                out.append(f"<strong>{inner}</strong>" if keep_bold else inner)
            elif name in ("textit", "emph"):
                body, i = _group(s, i)
                out.append(f"<cite>{tex2html(body, keep_bold)}</cite>")
            elif name == "ptitle":
                body, i = _group(s, i)
                out.append(tex2html(body, keep_bold))
            elif name == "sep":
                out.append("\x00SEP\x00")
            elif name == "sim":
                out.append("~")
            else:
                raise ValueError(f"unhandled LaTeX command \\{name} in {s[:80]!r}")
        elif c == "$":
            j = s.index("$", i + 1)
            math = s[i + 1:j]
            i = j + 1
            flush()
            m = re.fullmatch(r"_(\{?)([^{}]+)\1?\}?", math)
            if m:
                out.append(f"<sub>{html.escape(m.group(2))}</sub>")
            elif math.strip() == "\\sim":
                out.append("~")
            else:
                raise ValueError(f"unhandled math ${math}$")
        elif c in "{}":
            i += 1
        else:
            run.append(c)
            i += 1
    flush()
    return "".join(out)


def fields(item, keep_bold=False):
    """Split a CV item on \\sep into HTML fields."""
    return [f.strip() for f in tex2html(item, keep_bold).split("\x00SEP\x00")]


def first_person(s):
    for pat, rep in VOICE:
        s = re.sub(pat, rep, s)
    return s


def cap(s):
    return s[:1].upper() + s[1:] if s else s


# ------------------------------------------------------------------- CV parsing

def parse_cv(tex):
    """Return {block name: {"items": [...raw item strings], "note": str}}."""
    body = tex.split("\\begin{document}", 1)[1]
    parts = re.split(r"\\block\{([^}]*)\}", body)
    blocks = {}
    for name, content in zip(parts[1::2], parts[2::2]):
        note = re.search(r"\\note\{(.*?)\}\s*$", content, re.M)
        env = re.search(r"\\begin\{(itemize|etaremune)\}(.*?)\\end\{\1\}", content, re.S)
        items = []
        if env:
            for raw in re.split(r"^\s*\\item\s+", env.group(2), flags=re.M)[1:]:
                items.append(" ".join(raw.split()))
        blocks[name] = {"items": items, "note": note.group(1) if note else "", "raw": content}
    return blocks


def cv_date(s):
    """'2026 November' -> 'November 2026', '2026 Fall' -> 'Fall 2026', ranges spaced."""
    s = s.replace("\u2013", "--").replace("&amp;", "&")
    def one(t):
        t = t.strip()
        m = re.fullmatch(r"(\d{4}) (\w+)", t)
        return f"{m.group(2)} {m.group(1)}" if m else t
    if " -- " in s:
        return " \u2013 ".join(one(t) for t in s.split(" -- "))
    if "--" in s:
        return "\u2013".join(one(t) for t in s.split("--"))
    if " & " in s:
        return " and ".join(one(t) for t in s.split(" & "))
    return one(s)


def date_key(s):
    """(year, month) of the first date in a CV date string, month 0 if unknown."""
    m = re.match(r"(\d{4})(?: (\w+))?", s)
    if not m:
        return (0, 0)
    month = MONTHS.index(m.group(2)) + 1 if m.group(2) in MONTHS else 0
    return (int(m.group(1)), month)


# ------------------------------------------------------------------ publications

def bibcode(year, journal, volume, article, first_author):
    stem = BIBSTEMS.get(journal)
    if stem is None:
        raise ValueError(f"no ADS bibstem for journal {journal!r}: add it to BIBSTEMS")
    m = re.fullmatch(r"([A-Za-z]?)(\d+)", article)
    if not m:
        raise ValueError(f"cannot parse article id {article!r}")
    qual, page = m.group(1), m.group(2)
    if not qual and len(page) == 5:  # five-digit pages spill into the qualifier slot
        qual, page = page[0], page[1:]
    initial = unicodedata.normalize("NFKD", first_author)[0].upper()
    return f"{year}{stem:.<5}{volume:.>4}{qual or '.'}{page:.>4}{initial}"


def parse_paper(raw):
    k = raw.find("\\ptitle")
    if k < 0:
        raise ValueError(f"publication without \\ptitle: {raw[:80]!r}")
    authors_tex = raw[:k]
    title_tex, end = _group(raw, k + len("\\ptitle"))
    venue_tex = raw[end:].strip()
    year = re.search(r"\((\d{4})\)\.?\s*$", venue_tex).group(1)
    first = authors_tex.strip().split(",")[0].replace("\\textbf{", "").strip()
    paper = {
        "authors": tex2html(authors_tex.strip(), keep_bold=True),
        "title": tex2html(title_tex).rstrip("."),
        "year": year,
        "first_author": first.startswith("Attia"),
    }
    pre = re.match(r"Preprint, arXiv:(\S+) \(\d{4}\)\.?", venue_tex)
    if pre:
        paper["venue"] = f"Preprint, arXiv:{pre.group(1)}"
        paper["url"] = f"https://arxiv.org/abs/{pre.group(1)}"
        paper["preprint"] = True
        return paper
    jm = re.match(r"\\textit\{(.*?)\}\s+(\d+),\s*(\w+)\s*\((\d{4})\)", venue_tex)
    if not jm:
        raise ValueError(f"cannot parse venue {venue_tex!r}")
    journal = jm.group(1).replace("\\&", "&")
    volume, article = jm.group(2), jm.group(3)
    code = bibcode(year, journal, volume, article, first)
    paper["venue"] = f"<cite>{html.escape(journal)}</cite> {volume}, {article}"
    paper["bibcode"] = code
    paper["url"] = f"https://ui.adsabs.harvard.edu/abs/{quote(code, safe='')}/abstract"
    paper["preprint"] = False
    return paper


def render_papers(blk):
    papers = [parse_paper(r) for r in blk["items"]]
    n = len(papers)
    rows = []
    for k, p in enumerate(papers):
        tag = ' <span class="tag tag-first">first author</span>' if p["first_author"] else ""
        tag += ' <span class="tag">preprint</span>' if p["preprint"] else ""
        rows.append(
            f'<li value="{n - k}">'
            f'<a class="pub-title" href="{p["url"]}" target="_blank" rel="noopener">{p["title"]}</a>{tag}'
            f'<span class="pub-authors">{p["authors"]}</span>'
            f'<span class="pub-venue">{p["venue"]} ({p["year"]})</span></li>'
        )
    return '<ol class="pubs" reversed>\n' + "\n".join(rows) + "\n</ol>", papers


# --------------------------------------------------------------------- the rest

def dated_list(items, cls="dated", transform=None):
    rows = []
    for raw in items:
        f = fields(raw)
        date, rest = cv_date(f[0]), [x for x in f[1:] if x]
        text = transform(rest) if transform else ", ".join(rest)
        rows.append(f'<li><span class="when">{date}</span><span class="what">{text}</span></li>')
    return f'<ul class="{cls}">\n' + "\n".join(rows) + "\n</ul>"


def conference_text(rest):
    meeting, kind = rest[0], rest[1:]
    return f"{meeting}" + (f' <span class="kind">{", ".join(kind)}</span>' if kind else "")


def seminar_text(rest):
    return ", ".join(rest)


def sentence_text(rest):
    return first_person(cap(" ".join(rest)))


def plain_list(items, cls="plain"):
    rows = [f"<li>{first_person(tex2html(r))}</li>" for r in items]
    return f'<ul class="{cls}">\n' + "\n".join(rows) + "\n</ul>"


def code_entries(items):
    out = {}
    for raw in items:
        m = re.match(r"\\href\{([^}]*)\}\{([^}]*)\}:\s*(.*)$", raw)
        if m:
            out[m.group(2)] = (m.group(1), first_person(tex2html(m.group(3))))
    return out


def upcoming(blocks, today):
    now = (today.year, today.month)
    rows = []
    for name, label in (("contributions in international conferences", None), ("colloquium and seminar talks", "seminar")):
        for raw in blocks[name]["items"]:
            f = fields(raw)
            if date_key(f[0]) > now:  # month resolution: the current month counts as past
                rest = [x for x in f[1:] if x]
                kind = label or ", ".join(rest[1:])
                where = rest[0] if label is None else ", ".join(rest)
                rows.append((date_key(f[0]), cv_date(f[0]), where, kind))
    rows.sort()
    if not rows:
        return '<p class="muted">No talks scheduled at the moment.</p>'
    lis = [f'<li><span class="when">{d}</span><span class="what">{w} <span class="kind">{k}</span></span></li>' for _, d, w, k in rows]
    return '<ul class="dated">\n' + "\n".join(lis) + "\n</ul>"


def career_list(items):
    rows = []
    for raw in items:
        f = [x for x in fields(raw) if x]
        rows.append(f'<li><span class="when">{cv_date(f[0])}</span><span class="what">{"<br>".join(f[1:])}</span></li>')
    return '<ul class="dated compact">\n' + "\n".join(rows) + "\n</ul>"


def header_html(active):
    links = []
    for href, label in PAGES[1:]:
        cur = ' aria-current="page"' if href == active else ""
        links.append(f'<li><a href="{href}"{cur}>{label}</a></li>')
    links.append('<li><a href="assets/CV_Mara_Attia.pdf" target="_blank" rel="noopener">CV</a></li>')
    return (
        '<header class="site-header">\n'
        '  <div class="wrap bar">\n'
        '    <a class="brand" href="index.html">Mara Attia</a>\n'
        '    <button class="menu-btn" aria-expanded="false" aria-controls="site-nav" aria-label="Menu"><i class="fas fa-bars" aria-hidden="true"></i></button>\n'
        '    <nav id="site-nav"><ul>\n      ' + "\n      ".join(links) + "\n    </ul></nav>\n"
        "  </div>\n</header>"
    )


def footer_html(today):
    return (
        '<footer class="site-footer">\n'
        '  <div class="wrap">\n'
        f'    <p>&copy; {today.year} Mara Attia. Last updated {MONTHS[today.month - 1]} {today.year}.</p>\n'
        '    <p class="credits">JADE logo by Elsa Bersier (ESBDI). ZEPHYRUS logo by Emma Postolec (University of Groningen). '
        'Background art, cropped and dimmed, under <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">CC BY 4.0</a>: '
        'TRAPPIST-1, <a href="https://www.eso.org/public/images/eso1805a/" target="_blank" rel="noopener">ESO/M. Kornmesser</a>. '
        'CoRoT-7 b, <a href="https://www.eso.org/public/images/eso0933a/" target="_blank" rel="noopener">ESO/L. Calçada</a>. '
        'GJ 436 b, <a href="https://esahubble.org/images/heic1515a/" target="_blank" rel="noopener">NASA, ESA, STScI, and G. Bacon</a>. '
        '2M1510 (AB) b, <a href="https://www.eso.org/public/images/eso2508a/" target="_blank" rel="noopener">ESO/L. Calçada</a>. '
        'Milky Way, <a href="https://www.eso.org/public/images/eso0932a/" target="_blank" rel="noopener">ESO/S. Brunier</a>.</p>\n'
        "  </div>\n</footer>"
    )


# ------------------------------------------------------------------- main build

def fill(page_text, name, content):
    pat = re.compile(rf"(<!-- BEGIN {re.escape(name)} -->)(.*?)(<!-- END {re.escape(name)} -->)", re.S)
    if not pat.search(page_text):
        raise ValueError(f"marker {name!r} not found")
    return pat.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", page_text, count=1)


def build(cv_path, today, check=False):
    blocks = parse_cv(Path(cv_path).read_text(encoding="utf-8"))
    papers_html, papers = render_papers(blocks["published articles"])
    codes = code_entries(blocks["published open-source codes"]["items"])
    other_codes = []
    for name in ("PROTEUS", "TripleBHMergerPredictor"):
        url, desc = codes[name]
        other_codes.append(f'<li><a href="{html.escape(url)}" target="_blank" rel="noopener">{name}</a>: {desc}</li>')

    regions = {
        "index.html": {
            "upcoming": upcoming(blocks, today),
            "positions": career_list([i for i in blocks["positions"]["items"] if not any(k in i for k in HOME_POSITIONS_SKIP)]),
            "education": career_list(blocks["education"]["items"]),
            "awards": career_list(blocks["awards/grants"]["items"]),
        },
        "papers.html": {
            "papers-note": f'<p class="muted">{tex2html(blocks["published articles"]["note"])}</p>',
            "papers": papers_html,
        },
        "talks.html": {
            "upcoming": upcoming(blocks, today),
            "conferences": dated_list(blocks["contributions in international conferences"]["items"], transform=conference_text),
            "seminars": dated_list(blocks["colloquium and seminar talks"]["items"], transform=seminar_text),
            "outreach": plain_list(blocks["outreach"]["items"]),
        },
        "teaching.html": {
            "teaching": dated_list(blocks["teaching"]["items"], transform=sentence_text),
            "supervision": dated_list(blocks["supervision"]["items"], transform=sentence_text),
        },
        "codes.html": {
            "codes-other": '<ul class="plain">\n' + "\n".join(other_codes) + "\n</ul>",
        },
    }

    for page, _ in PAGES:
        path = ROOT / page
        text = path.read_text(encoding="utf-8")
        new = fill(text, "site-header", header_html(page))
        new = fill(new, "site-footer", footer_html(today))
        for name, content in regions.get(page, {}).items():
            new = fill(new, name, content)
        status = "unchanged" if new == text else "updated"
        if not check and new != text:
            path.write_text(new, encoding="utf-8")
        print(f"{page}: {status}{' (check only)' if check and new != text else ''}")
    return papers


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cv", default=os.environ.get("CV_TEX"), help="path to cv_attia_full.tex (or set CV_TEX)")
    ap.add_argument("--date", help="build date YYYY-MM-DD (default: today), sets 'upcoming' and the footer")
    ap.add_argument("--check", action="store_true", help="report which pages would change, write nothing")
    ap.add_argument("--bibcodes", action="store_true", help="print the computed ADS bibcodes")
    args = ap.parse_args()
    if not args.cv:
        sys.exit("give the CV path with --cv or the CV_TEX environment variable")
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    papers = build(Path(args.cv).expanduser(), today, check=args.check)
    if args.bibcodes:
        for p in papers:
            print(p.get("bibcode") or p["url"])


if __name__ == "__main__":
    main()
