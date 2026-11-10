"""
Markdown for emails: the HTML part, with the house style's inline styles (Outlook ignores <style> for most things),
and the plain text part. Links are resolved: /docs/<page> to the server's docs, app:/<path> to the web app.
Raw HTML in the Markdown is escaped, not rendered. See backend/notifications/STYLE.md.
"""
import re
import html as html_lib
from typing import Callable, Tuple

from markdown_it import MarkdownIt


FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "Consolas,'SFMono-Regular',Menlo,'Liberation Mono',monospace"


def text_style(size=15, line=24, color='#404854', weight=None) -> str:
  return (f"font-family:{FONT};font-size:{size}px;line-height:{line}px;mso-line-height-rule:exactly;"
          + (f"font-weight:{weight};" if weight else "") + f"color:{color};")


# Opening tags as markdown-it writes them, and the house style's version
STYLES = {
  'p': f"margin:0 0 16px 0;{text_style()}",
  'li_p': f"margin:0;{text_style()}",
  'ul': "margin:0 0 16px 24px;padding:0;",
  'ol': "margin:0 0 16px 24px;padding:0;",
  'li': f"margin:0 0 6px 0;{text_style()}",
  'h2': f"margin:24px 0 8px 0;{text_style(17, 24, '#1C2127', 600)}",
  'h3': f"margin:20px 0 6px 0;{text_style(15, 22, '#1C2127', 600)}",
  'a': "color:#1B69B6;text-decoration:none;",
  'strong': "font-weight:600;color:#1C2127;",
  'code': f"font-family:{MONO};font-size:13px;background-color:#F6F7F9;color:#1C2127;padding:1px 4px;",
  'blockquote': "margin:0 0 16px 0;padding:0 0 0 12px;border-left:3px solid #E5E8EB;",
  'hr': "border:0;border-top:1px solid #E5E8EB;margin:24px 0;height:0;",
}


def code_block(code: str) -> str:
  """Word ignores <pre>'s padding and background: a table cell"""
  lines = html_lib.escape(code.rstrip('\n'), quote=False).replace(' ', '&nbsp;').split('\n')
  return (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px 0;">'
    '<tr><td bgcolor="#F6F7F9" class="mono" style="background-color:#F6F7F9;border:1px solid #E5E8EB;padding:10px 14px;'
    f'font-family:{MONO};font-size:13px;line-height:20px;mso-line-height-rule:exactly;color:#1C2127;word-break:break-all;">'
    + '<br>'.join(lines) +
    '</td></tr></table>'
  )


def _markdown() -> MarkdownIt:
  return MarkdownIt('commonmark', {'html': False, 'typographer': False}).enable(['strikethrough'])


def resolve_links(tokens, resolve_url: Callable[[str], str]):
  for token in tokens:
    if token.type == 'link_open':
      token.attrSet('href', resolve_url(token.attrGet('href') or ''))
    if token.children:
      resolve_links(token.children, resolve_url)


def render_html(markdown: str, resolve_url: Callable[[str], str]) -> str:
  md = _markdown()
  tokens = md.parse(markdown)
  resolve_links(tokens, resolve_url)
  # fenced and indented code: a table, not <pre>
  def fence(self, tokens, idx, options, env):
    return code_block(tokens[idx].content)
  md.add_render_rule('fence', fence)
  md.add_render_rule('code_block', fence)
  html = md.renderer.render(tokens, md.options, {})
  # paragraphs in list items (loose lists) don't add their own margin
  html = re.sub(r'<li>\s*<p>', f'<li style="{STYLES["li"]}"><p style="{STYLES["li_p"]}">', html)
  for tag in ('p', 'ul', 'ol', 'li', 'h2', 'h3', 'strong', 'code', 'blockquote'):
    html = re.sub(rf'<{tag}(\s[^>]*)?>', lambda m, tag=tag: f'<{tag}{m.group(1) or ""} style="{STYLES[tag]}">', html)
  html = html.replace('<hr />', f'<hr style="{STYLES["hr"]}" />')
  html = re.sub(r'<a href="', f'<a style="{STYLES["a"]}" href="', html)
  # h1 is the email's title: lower the others
  html = re.sub(r'<h1>(.*?)</h1>', lambda m: f'<h2 style="{STYLES["h2"]}">{m.group(1)}</h2>', html)
  for level in (4, 5, 6):
    html = re.sub(rf'<h{level}>(.*?)</h{level}>', lambda m: f'<h3 style="{STYLES["h3"]}">{m.group(1)}</h3>', html)
  return html.strip()


def render_text(markdown: str, resolve_url: Callable[[str], str]) -> str:
  """The plain text part: Markdown is already readable, we only spell out the links"""
  md = _markdown()
  tokens = md.parse(markdown)
  resolve_links(tokens, resolve_url)
  out = []
  lists = []  # the list numbers, None for bullets
  def inline(children):
    text, href = [], []
    for child in children:
      if child.type == 'text':
        text.append(child.content)
      elif child.type == 'code_inline':
        text.append(f"`{child.content}`")
      elif child.type in ('softbreak', 'hardbreak'):
        text.append('\n')
      elif child.type == 'link_open':
        href.append(child.attrGet('href'))
      elif child.type == 'link_close':
        url = href.pop() if href else None
        if url and not url.startswith('mailto:'):
          text.append(f" ({url})")
      elif child.type == 'image':
        text.append(child.content)
    return ''.join(text)
  for token in tokens:
    if token.type == 'bullet_list_open':
      lists.append(None)
    elif token.type == 'ordered_list_open':
      lists.append(int(token.attrGet('start') or 1))
    elif token.type in ('bullet_list_close', 'ordered_list_close'):
      lists.pop()
      if not lists:
        out.append('')
    elif token.type == 'list_item_open':
      depth = '  ' * (len(lists) - 1)
      if lists[-1] is None:
        out.append(f"{depth}- ")
      else:
        out.append(f"{depth}{lists[-1]}. ")
        lists[-1] += 1
    elif token.type == 'inline':
      text = inline(token.children)
      if lists and out and out[-1].rstrip().endswith(('-', '.')) and out[-1].endswith(' '):
        indent = ' ' * len(out[-1])
        out[-1] += text.replace('\n', '\n' + indent)
      else:
        out.append(text)
    elif token.type in ('paragraph_close', 'heading_close') and not lists:
      out.append('')
    elif token.type in ('fence', 'code_block'):
      out.append('\n'.join('    ' + line for line in token.content.rstrip('\n').split('\n')))
      out.append('')
    elif token.type == 'hr':
      out.append('---')
      out.append('')
  text = '\n'.join(out)
  return re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'


def render(markdown: str, resolve_url: Callable[[str], str]) -> Tuple[str, str]:
  """Returns (html, text)"""
  return render_html(markdown, resolve_url), render_text(markdown, resolve_url)
