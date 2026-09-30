"""Public Blind insurance board -> ntfy. Python standard library only."""
import hashlib
import json
import os
from pathlib import Path
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, unquote
from urllib.request import Request, urlopen

BOARD = "https://www.teamblind.com/kr/topics/%EB%B3%B4%ED%97%98"
STATE = Path("state/seen.json")
VOID = set("area base br col embed hr img input link meta param source track wbr".split())


class Posts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.current = None
        self.posts = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID:
            self.stack.append((tag, attrs.get("class", "").split()))
        in_list = any("article-list" in classes for _, classes in self.stack)
        in_heading = any(t == "h3" for t, _ in self.stack)
        if tag == "a" and in_list and in_heading:
            url = urljoin(BOARD, attrs.get("href", ""))
            parts = urlsplit(url)
            if parts.hostname == "www.teamblind.com" and parts.path.startswith("/kr/post/"):
                self.current = ["https://www.teamblind.com" + parts.path, []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            url, chunks = self.current
            title = " ".join("".join(chunks).split())
            if title:
                self.posts[url] = title
            self.current = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def fetch_posts():
    request = Request(BOARD, headers={"User-Agent": "Mozilla/5.0 (compatible; InsuranceBoardMonitor/1.0)"})
    with urlopen(request, timeout=40) as response:
        if urlsplit(response.url).path.rstrip("/") != urlsplit(BOARD).path:
            raise RuntimeError("Board redirected; check access in GitHub Actions.")
        html = response.read().decode("utf-8")
    parser = Posts()
    parser.feed(html)
    if not parser.posts:
        raise RuntimeError("No board titles found. Access may be blocked or page structure changed. State was preserved.")
    return parser.posts


def key(url):
    post_id = unquote(urlsplit(url).path).rsplit("-", 1)[-1]
    return hashlib.sha256(post_id.encode()).hexdigest()


def save(seen):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "seen": sorted(seen)}, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)


def app_link(url):
    """Use Blind's share route; iOS decides whether to hand it to the app."""
    parts = urlsplit(url)
    if parts.hostname == "www.teamblind.com" and parts.path.startswith("/kr/post/"):
        post_id = unquote(parts.path).rsplit("-", 1)[-1]
        if re.fullmatch(r"[A-Za-z0-9]+", post_id):
            return "https://www.teamblind.com/kr/s/" + post_id
    return url


def notify(title, message, click=BOARD):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", topic):
        raise RuntimeError("Set NTFY_TOPIC secret: 16-128 letters, digits, underscores or hyphens.")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("NTFY_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps({"topic": topic, "title": title, "message": message,
                       "click": app_link(click), "priority": 3}, ensure_ascii=False).encode("utf-8")
    request = Request("https://ntfy.sh/", data=body, headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:
        response.read()


def main():
    if os.environ.get("SEND_TEST", "false").lower() == "true":
        notify("블라인드 앱 연결 테스트", "이 알림을 눌러 블라인드 앱에서 게시글이 열리는지 확인합니다.",
               "https://www.teamblind.com/kr/s/gdbzzh1x")
        print("Test notification sent.")
    posts = fetch_posts()
    if not STATE.exists():
        seen = {key(url) for url in posts}
        save(seen)
        notify("블라인드 보험 감시 시작", f"기존 글 {len(posts)}개를 기준으로 저장했습니다. 이후 처음 발견한 글부터 알립니다.")
        print(f"Initialized with {len(posts)} posts. Existing posts were not sent.")
        return
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("seen"), list):
        raise RuntimeError("Invalid state file; restore previous state before running again.")
    seen = set(data["seen"])
    sent = 0
    for url, title in reversed(list(posts.items())):
        identifier = key(url)
        if identifier in seen:
            continue
        notify("블라인드 보험 새 글", title[:500], url)
        seen.add(identifier)
        save(seen)
        sent += 1
    print(f"Checked {len(posts)} posts; sent {sent} notifications.")


if __name__ == "__main__":
    main()
