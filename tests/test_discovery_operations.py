import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from atlas_terminal.errors import AtlasError

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://docs.example"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crawl = load_script("check_public_docs")
deployment = load_script("prepare_remote_deploy")
workflow = load_script("run_research_workflow")


def public_site(*, robots=None, page_change=None, sitemap=None):
    requests = []

    def handle(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(
                200,
                text=robots or "User-agent: *\nAllow: /\n",
                headers={"Set-Cookie": "audit_cookie=fictional; Path=/"},
            )
        if path == "/sitemap.xml":
            content = sitemap or (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{ORIGIN}/</loc></url>"
                f"<url><loc>{ORIGIN}/docs/citations.html</loc></url></urlset>"
            )
            return httpx.Response(200, content=content, headers={"Content-Type": "application/xml"})
        headers = {"Content-Type": "text/html; charset=utf-8"}
        body = (
            f'<html><head><link rel="canonical" href="{ORIGIN}{path}">'
            '<meta name="robots" content="index,follow"></head>'
            "<body><article>Atlas Terminal documentation</article></body></html>"
        )
        if page_change:
            return page_change(request, headers, body)
        return httpx.Response(200, text=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False), requests


def test_crawl_verifies_every_sitemap_page_and_labels_simulated_user_agents():
    client, requests = public_site()
    with client:
        result = crawl.audit(client, ORIGIN)
    assert result["status"] == "PASS" and result["sitemap_pages"] == 2
    searched = [r.url.path for r in requests if r.headers["User-Agent"].startswith("OAI-SearchBot")]
    assert "/docs/citations.html" in searched
    assert result["user_agent_simulation"]
    assert not result["official_crawler_ip_verified"]
    assert not result["search_indexing_verified"] and not result["model_citation_measured"]


@pytest.mark.parametrize("kind", ["header", "meta", "canonical", "login", "redirect", "denied"])
def test_crawl_fails_for_index_blocks_wrong_pages_and_http_failures(kind):
    def change(request, headers, body):
        if kind == "header":
            headers["X-Robots-Tag"] = "OAI-SearchBot: noindex"
        if kind == "meta":
            body = body.replace("index,follow", "noindex,follow")
        if kind == "canonical":
            body = body.replace(ORIGIN, "https://other.example")
        if kind == "login":
            body = "<html><title>Authentication required</title></html>"
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example/login"})
        if kind == "denied":
            return httpx.Response(403, text="private response with SECRET_SENTINEL")
        return httpx.Response(200, text=body, headers=headers)

    client, requests = public_site(page_change=change)
    with client:
        result = crawl.audit(client, ORIGIN)
    assert result["status"] == "FAIL"
    assert "SECRET_SENTINEL" not in json.dumps(result)
    assert all(str(request.url).startswith(ORIGIN) for request in requests)


def test_crawl_obeys_search_specific_robots_and_longest_allow_rule():
    robots = "User-agent: *\nAllow: /\nUser-agent: OAI-SearchBot\nDisallow: /docs/\n"
    client, _ = public_site(robots=robots)
    with client:
        report = crawl.audit(client, ORIGIN)
    assert any(item.get("code") == "ROBOTS_DISALLOWS" for item in report["checks"])
    rules = "User-agent: *\nDisallow: /*.html$\nAllow: /docs/citations.html$\n"
    assert crawl.robots_allows(rules, "OAI-SearchBot", "/docs/citations.html")
    assert not crawl.robots_allows(rules, "OAI-SearchBot", "/docs/hidden.html")
    assert crawl.robots_allows(rules, "OAI-SearchBot", "/docs/hidden.html/other")
    empty_specific = "User-agent: *\nDisallow: /\nUser-agent: OAI-SearchBot\nDisallow:\n"
    assert crawl.robots_allows(empty_specific, "OAI-SearchBot", "/docs/citations.html")
    assert not crawl.robots_allows(empty_specific, "Googlebot", "/docs/citations.html")


def test_crawl_rejects_external_sitemap_without_requesting_external_host():
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://external.example/</loc></url></urlset>"
    )
    client, requests = public_site(sitemap=sitemap)
    with client:
        report = crawl.audit(client, ORIGIN)
    assert report["status"] == "FAIL"
    assert any(item.get("code") == "SITEMAP_ORIGIN_MISMATCH" for item in report["checks"])
    assert all(request.url.host == "docs.example" for request in requests)


def test_crawl_network_failure_never_exposes_transport_exception():
    def fail(request):
        raise httpx.ConnectError("SECRET_SENTINEL", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        report = crawl.audit(client, ORIGIN)
    assert report["status"] == "FAIL" and "SECRET_SENTINEL" not in json.dumps(report)
    assert "NETWORK_ERROR" in json.dumps(report)
    stream = io.StringIO()
    report["checks"] = [
        {
            "status": "FAIL",
            "code": "NETWORK_ERROR",
            "agent": "audit",
            "url": "https://docs.example/\n::add-mask::text%",
        }
    ]
    crawl.emit_failure_annotations(report, stream)
    annotation = stream.getvalue()
    assert annotation.count("\n") == 1
    assert "%0A::add-mask::text%25" in annotation


def test_deploy_files_bind_loopback_keep_state_and_require_bearer_by_default(tmp_path):
    output = tmp_path / "deployment"
    result = deployment.prepare("https://mcp.example", output, "public")
    assert result["status"] == "PREPARED" and not result["deployed"]
    service = (output / "atlas-mcp.service").read_text()
    assert "--host 127.0.0.1" in service and "--provider public" in service
    assert "StateDirectory=atlas-terminal" in service
    assert "StateDirectoryMode=0700" in service and "UMask=0077" in service
    assert "--allow-anonymous" not in service and "__ORIGIN__" not in service
    assert "https://mcp.example" in service
    assert "mcp.example {" in (output / "Caddyfile").read_text()
    with pytest.raises(FileExistsError):
        deployment.prepare("https://mcp.example", output)


def test_fixture_and_anonymous_deployment_are_explicit(tmp_path):
    output = tmp_path / "deployment"
    result = deployment.prepare("https://mcp.example", output, anonymous=True)
    assert result["provider"] == "fixture" and result["authentication"] == "anonymous"
    assert "--allow-anonymous" in (output / "atlas-mcp.service").read_text()


@pytest.mark.parametrize(
    "origin",
    [
        "https://mcp.example\nExecStart=/bin/false",
        "https://mcp.example/{malformed}",
        "https://user:password@mcp.example",
        "http://mcp.example",
        "https://mcp.example:8443",
        "https://127.0.0.1",
        "https://mcp.example?token=secret",
    ],
)
def test_deploy_rejects_invalid_origins_before_writing_files(tmp_path, origin):
    with pytest.raises((ValueError, AtlasError)):
        deployment.prepare(origin, tmp_path / "deployment")
    assert not (tmp_path / "deployment").exists()


async def test_workflow_stops_before_tool_calls_for_unexpected_server_surface():
    class UnexpectedServer:
        async def initialize(self):
            return SimpleNamespace(server_info=SimpleNamespace(version="test"))

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="atlas_purchase")])

        async def call_tool(self, *_):
            pytest.fail("Unexpected server surface must stop before any tool calls")

    result = await workflow.evaluate(UnexpectedServer(), synthetic=True)
    assert result["status"] == "FAIL" and result["calls"] == []


def test_remote_workflow_rejects_query_tokens_and_non_https_before_connecting():
    for url in [
        "https://mcp.example/mcp?token=secret",
        "https://user:pass@mcp.example/mcp",
        "http://mcp.example/mcp",
    ]:
        with pytest.raises((ValueError, AtlasError)):
            workflow.remote_url(url)


def test_workflow_evidence_identifiers_must_be_valid():
    with pytest.raises(ValueError):
        list(workflow.evidence_ids({"inputs": [{"evidence_id": "invalid"}]}))
