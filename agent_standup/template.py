"""Static page assets for the generated HTML report.

Markup and CSS only: the page contains no ``<script>`` element at all, and
every asset is inlined, so the report makes no network request of any kind.
The palette and tile/card classes follow cc-stats' dashboard template so the
two tools' generated pages read as one family.
"""

# 16x16 PNG, identical to cc-stats' favicon. A PNG data URI avoids the xmlns
# URL an inline SVG icon would need.
FAVICON = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAA"
           "JUlEQVR42mMQEpEiCTEMAg2W7U8haIhpQFY0lDTgUjSAGmiV+AAWD5fcS+ZeXAAAAABJ"
           "RU5ErkJggg==")

CSS = """
:root {
  color-scheme: dark;
  --bg: #12141a; --panel: #191c24; --panel-2: #1f232d; --line: #2b303c;
  --fg: #e6e8ee; --fg-dim: #99a0b0; --grid: #2b303c;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70;
}
@media (prefers-color-scheme: light) {
  :root {
    color-scheme: light;
    --bg: #f6f7f9; --panel: #ffffff; --panel-2: #eef0f5; --line: #d3d8e2;
    --fg: #1b1f28; --fg-dim: #555f70; --grid: #e4e8ef;
    --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
.topbar {
  position: sticky; top: 0; z-index: 5; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 12px 20px;
}
.topbar h1 { margin: 0 0 3px; font-size: 16px; font-weight: 600; }
.crumbs { color: var(--fg-dim); font-size: 12px; display: flex; flex-wrap: wrap; gap: 10px; }
.wrap { max-width: 1000px; margin: 0 auto; padding: 18px 20px 30px; }
.tiles {
  display: grid; gap: 10px; margin-bottom: 16px;
  grid-template-columns: repeat(auto-fit, minmax(min(150px, 100%), 1fr));
}
.tile {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: 9px; padding: 10px 12px;
}
.tile b { display: block; font-size: 19px; font-variant-numeric: tabular-nums; }
.tile span {
  color: var(--fg-dim); font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
}
.tile.warn { border-color: var(--s2); }
.tile.warn b { color: var(--s2); }
.tile.span2 { grid-column: span 2; }
.tile.span2 b { font-size: 14px; }
h2.section { font-size: 13px; text-transform: uppercase; letter-spacing: .05em;
  color: var(--fg-dim); margin: 22px 2px 10px; }
.cards {
  display: grid; gap: 14px;
  grid-template-columns: repeat(auto-fit, minmax(min(360px, 100%), 1fr));
}
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 9px;
  padding: 12px 14px 10px; min-width: 0;
}
.card h3 { margin: 0 0 8px; font-size: 13.5px; font-weight: 600; word-break: break-word; }
.card dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; }
.card dt { color: var(--fg-dim); font-size: 11.5px; }
.card dd { margin: 0; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.card .note { margin: 8px 0 0; color: var(--fg-dim); font-size: 11.5px; }
.commits { list-style: none; margin: 8px 0 0; padding: 0; }
.commits li {
  padding: 6px 0; border-top: 1px solid var(--line); font-size: 12.5px;
  word-break: break-word;
}
.commits li:first-child { border-top: none; }
.commits .stat { color: var(--fg-dim); font-size: 11px; margin-left: 6px; white-space: nowrap; }
.other-line { margin: 8px 0 0; color: var(--fg-dim); font-size: 11.5px; }
.foot {
  max-width: 1000px; margin: 0 auto; padding: 12px 20px 30px;
  border-top: 1px solid var(--line); color: var(--fg-dim); font-size: 11.5px;
  display: flex; flex-wrap: wrap; gap: 10px;
}
.foot .warn-badge { color: var(--s2); }
@media (max-width: 560px) {
  .wrap, .foot { padding-left: 12px; padding-right: 12px; }
}
"""

TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="agent-standup %%VERSION%%">
<title>agent-standup &middot; %%TITLE%%</title>
<link rel="icon" href="%%FAVICON%%">
<style>%%CSS%%</style>
</head>
<body>
<header class="topbar">
  <h1>Standup report</h1>
  <div class="crumbs">%%CRUMBS%%</div>
</header>
<main class="wrap">
  <section class="tiles">%%TILES%%</section>
  %%PROJECTS%%
  %%REPOS%%
</main>
<footer class="foot">%%FOOTER%%</footer>
</body>
</html>
"""
