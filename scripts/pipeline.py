#!/usr/bin/env python3
"""
Content Pipeline Orchestrator
Runs the full agent pipeline: Research → Writer → Editor → Publisher → Distribute

Usage:
  python3 pipeline.py                    # full cycle (daily)
  python3 pipeline.py --stage research   # run one stage
  python3 pipeline.py --dry-run          # simulate without publishing
"""

import json, os, sys, subprocess, textwrap, re, time
from pathlib import Path
from datetime import datetime

HOME = Path.home()
PROJECT = HOME / "projects" / "content-pipeline"
CONFIG = json.loads((PROJECT / "config.json").read_text())
BLOG = PROJECT / "blog"

def log(msg):
    print(f"[pipeline] {msg}", flush=True)

def load_agent_prompt(name):
    path = PROJECT / "agents" / f"{name}.md"
    content = path.read_text()
    # Extract instructions (everything after frontmatter)
    parts = content.split("---", 2)
    return parts[2].strip() if len(parts) > 2 else content.strip()

def call_llm(system, user, model="deepseek-v4-flash", temp=0.5):
    """Call DeepSeek API and return response text."""
    import urllib.request
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # Try to read from Hermes .env
        env_path = HOME / ".hermes" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip("\"'")
                    break

    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not found")

    payload_dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": temp,
        "max_tokens": 4096,
    }
    # Disable thinking mode for speed (Pro defaults to thinking=enabled)
    # Also allows temperature to work (thinking mode doesn't support temp)
    payload_dict["thinking"] = {"type": "disabled"}
    payload = json.dumps(payload_dict).encode()

    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )

    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            data = json.loads(resp.read())
            text = data["choices"][0]["message"]["content"]
            log(f"LLM call ({model}): {len(text)} chars, {data['usage']['total_tokens']} tokens")
            return text
        except Exception as e:
            log(f"LLM attempt {attempt+1} failed: {e}")
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def get_article_dir(slug):
    d = BLOG / slug
    d.mkdir(parents=True, exist_ok=True)
    return d

def md_to_html(md_text):
    """Minimal markdown-to-HTML conversion for our limited subset."""
    html = md_text

    # Code blocks
    html = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code>\2</code></pre>', html, flags=re.DOTALL)

    # Inline code
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)

    # Blockquotes
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)

    # Headers
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

    # Bold/italic
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)

    # Horizontal rules
    html = re.sub(r'^---$', r'<hr>', html, flags=re.MULTILINE)

    # Paragraphs: wrap remaining text
    lines = html.split('\n')
    result = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('<h') or stripped.startswith('<pre') or stripped.startswith('</pre') or stripped.startswith('<blockquote') or stripped.startswith('</blockquote') or stripped.startswith('<hr') or stripped == '':
            if in_block:
                result.append('</p>')
                in_block = False
            result.append(line)
            continue
        if not in_block:
            result.append('<p>')
            in_block = True
            result.append(stripped)
        else:
            result.append(stripped)

    if in_block:
        result.append('</p>')

    return '\n'.join(result)

def slugify(title):
    s = title.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s[:60].strip('-')

def parse_metadata(md_text):
    """Extract title, excerpt, tags from markdown."""
    title = ""
    excerpt = ""
    tags = []

    lines = md_text.split('\n')
    for line in lines:
        if line.startswith('# ') and not title:
            title = line[2:].strip()
        if line.startswith('> ') and not excerpt:
            excerpt = line[2:].strip()

    # Find tags (section headers)
    for line in lines:
        m = re.match(r'^## Tags?:?\s*(.+)$', line, re.IGNORECASE)
        if m:
            tags = [t.strip() for t in m.group(1).split(',')]
            break

    if not excerpt:
        excerpt = title

    return title or "Untitled", excerpt, tags

def extract_slug_title(md_text):
    """Try to extract slug from # Title."""
    lines = md_text.split('\n')
    for line in lines:
        m = re.match(r'^\*\*Slug:\*\*\s*(.+)$', line)
        if m:
            return m.group(1).strip()
    for line in lines:
        if line.startswith('# '):
            return slugify(line[2:].strip())
    return f"post-{int(time.time())}"

def run_pipeline(dry_run=False):
    today = datetime.now().strftime("%Y-%m-%d")
    output_dir = PROJECT / "output"
    output_dir.mkdir(exist_ok=True)

    # ── STAGE 1: RESEARCH ──
    log("=== STAGE 1: Research ===")
    research_prompt = load_agent_prompt("research")
    research_instruction = (
        "Find a compelling topic for an article on personal development and growth. "
        "Scan current trends, psychology research, and counterintuitive angles. "
        "Produce a topic brief following the format in your instructions.\n\n"
        "Today's date: " + today + "\n\n"
        "Focus on: a fresh angle that most personal development content misses. "
        "Avoid overdone topics (discipline, morning routines, gratitude). "
        "Prefer tension — something people believe that might be wrong."
    )

    brief = call_llm(research_prompt, research_instruction,
                     model=CONFIG["agents"]["research"]["model"],
                     temp=CONFIG["agents"]["research"]["temperature"])
    (output_dir / "brief.md").write_text(brief)
    log(f"Brief written ({len(brief)} chars)")

    # Extract slug and title
    slug = extract_slug_title(brief)

    # ── STAGE 2: WRITER ──
    log("=== STAGE 2: Writer ===")
    writer_prompt = load_agent_prompt("writer")
    writer_instruction = (
        "Write a full article based on this topic brief.\n\n"
        "BRIEF:\n" + brief + "\n\n"
        f"Slug: {slug}\n"
        "Tone: direct, evidence-driven, no fluff. Write like you're telling a smart friend something they need to hear."
    )

    draft = call_llm(writer_prompt, writer_instruction,
                     model=CONFIG["agents"]["writer"]["model"],
                     temp=CONFIG["agents"]["writer"]["temperature"])
    (output_dir / "draft.md").write_text(draft)
    log(f"Draft written ({len(draft)} chars)")

    # ── STAGE 3: EDITOR ──
    log("=== STAGE 3: Editor ===")
    editor_prompt = load_agent_prompt("editor")
    editor_instruction = (
        "Edit this draft article. Apply the quality rubric."
        "Return your editor's notes + final version.\n\n"
        "DRAFT:\n" + draft
    )

    edited = call_llm(editor_prompt, editor_instruction,
                      model=CONFIG["agents"]["editor"]["model"],
                      temp=CONFIG["agents"]["editor"]["temperature"])

    # Extract the final version (everything after "## Final Version")
    final_md = edited
    if "## Final Version" in edited:
        final_md = edited.split("## Final Version", 1)[1].strip()
    (output_dir / "final.md").write_text(final_md)
    log(f"Final version written ({len(final_md)} chars)")

    # Extract metadata
    title, excerpt, tags = parse_metadata(final_md)
    if not title or title == "Untitled":
        # Try to extract from brief
        for line in brief.split('\n'):
            if line.startswith('# '):
                title = line[2:].strip()
                break

    # ── STAGE 4: PUBLISH (skip if dry-run) ──
    log("=== STAGE 4: Publisher ===")
    if dry_run:
        log("[DRY-RUN] Would publish now")
    else:
        # Create article directory
        article_dir = get_article_dir(slug)

        # Convert to HTML
        body_html = md_to_html(final_md)

        # Build full article HTML using template
        template = (PROJECT / "templates" / "article.html")
        if template.exists():
            article_html = template.read_text()
            article_html = article_html.replace("{{TITLE}}", title)
            article_html = article_html.replace("{{DATE}}", today)
            article_html = article_html.replace("{{BODY}}", body_html)
            article_html = article_html.replace("{{TAGS}}", ", ".join(tags))
            read_time = max(3, len(final_md.split()) // 200)
            article_html = article_html.replace("{{READ_TIME}}", str(read_time))
            article_html = article_html.replace("{{SLUG}}", slug)
        else:
            # Fallback minimal template
            article_html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title} — ungrowth</title><link rel="stylesheet" href="/style.css"></head>
<body class="article-page">
<nav><a href="/">← ungrowth</a></nav>
<article>
<header><h1>{title}</h1><div class="meta">{today} · {read_time} min read</div></header>
{body_html}
</article>
</body>
</html>"""

        (article_dir / "index.html").write_text(article_html)
        log(f"Article written: blog/{slug}/index.html")

        # Update articles.json
        articles_path = BLOG / "articles.json"
        articles = []
        if articles_path.exists():
            articles = json.loads(articles_path.read_text())

        new_entry = {
            "title": title,
            "slug": slug,
            "date": today,
            "excerpt": excerpt[:200],
            "tags": tags or ["growth"],
            "readTime": max(3, len(final_md.split()) // 200),
        }
        # Prepend (newest first)
        articles.insert(0, new_entry)
        articles_path.write_text(json.dumps(articles, indent=2))
        log(f"articles.json updated ({len(articles)} total)")

        # Git commit and push
        try:
            subprocess.run(["git", "add", "."], cwd=PROJECT, capture_output=True, timeout=30)
            subprocess.run(
                ["git", "commit", "-m", f"publish: {title}"],
                cwd=PROJECT, capture_output=True, timeout=30
            )
            push = subprocess.run(["git", "push"], cwd=PROJECT, capture_output=True, timeout=60)
            if push.returncode == 0:
                log(f"Pushed to GitHub")
            else:
                log(f"Git push: {push.stderr.decode()[:200]}")
        except Exception as e:
            log(f"Git: {e}")

    # ── STAGE 5: DISTRIBUTE — LinkedIn + Newsletter ──
    log("=== STAGE 5: Distribute ===")
    dist_dir = output_dir / "distribution"
    dist_dir.mkdir(exist_ok=True)

    if dry_run:
        log("[DRY-RUN] Would generate LinkedIn post + newsletter draft")
    else:
        # LinkedIn post generation
        try:
            linkedin_prompt = load_agent_prompt("linkedin")
            linkedin_instruction = (
                f"Generate a LinkedIn post for this article.\n\n"
                f"TITLE: {title}\n"
                f"ARTICLE:\n{final_md}\n\n"
                f"Produce a LinkedIn post and comment thread following the format in your instructions."
            )
            linkedin_post = call_llm(linkedin_prompt, linkedin_instruction,
                                     model="deepseek-v4-flash", temp=0.4)
            (dist_dir / f"linkedin-{slug}.md").write_text(linkedin_post)
            log(f"LinkedIn post generated")
        except Exception as e:
            log(f"LinkedIn generation failed: {e}")

        # Newsletter draft
        try:
            nl_prompt = load_agent_prompt("newsletter")
            nl_instruction = (
                f"Generate a newsletter draft for this article.\n\n"
                f"TITLE: {title}\n"
                f"SLUG: {slug}\n"
                f"URL: https://mopiagent-ctrl.github.io/ungrowth/{slug}/\n"
                f"ARTICLE:\n{final_md}"
            )
            nl_draft = call_llm(nl_prompt, nl_instruction,
                                model="deepseek-v4-flash", temp=0.5)
            (dist_dir / f"newsletter-{slug}.md").write_text(nl_draft)
            log(f"Newsletter draft generated")
        except Exception as e:
            log(f"Newsletter generation failed: {e}")

        log(f"Distribution files in output/distribution/")

    log("=== Pipeline complete ===")
    print(f"\n📄 {title}")
    print(f"   Slug: {slug}")
    print(f"   blog/{slug}/")
    print()

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    stage = None
    if "--stage" in sys.argv:
        idx = sys.argv.index("--stage")
        if idx + 1 < len(sys.argv):
            stage = sys.argv[idx + 1]

    if stage:
        log(f"Running single stage: {stage}")
        # TODO: implement single-stage
    else:
        run_pipeline(dry_run=dry_run)
