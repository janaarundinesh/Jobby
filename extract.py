"""Extract job details from a posting URL.

Uses schema.org JobPosting JSON-LD when a site publishes it (Greenhouse, Lever,
Workable, many company career pages), then falls back to OpenGraph metadata.
Standard library only; no external services.
"""

import html
import http.client
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 2_000_000
TIMEOUT_SECONDS = 12
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SOURCE_HOSTS = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "glassdoor.com": "Glassdoor",
    "ziprecruiter.com": "ZipRecruiter",
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "workable.com": "Workable",
    "myworkdayjobs.com": "Workday",
    "smartrecruiters.com": "SmartRecruiters",
    "ashbyhq.com": "Ashby",
    "bamboohr.com": "BambooHR",
    "wellfound.com": "Wellfound",
    "remoteok.com": "Remote OK",
    "weworkremotely.com": "We Work Remotely",
    "monster.com": "Monster",
    "hackernews.com": "Hacker News",
    "ycombinator.com": "Work at a Startup",
}

CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$", "AUD": "A$",
    "INR": "₹", "JPY": "¥", "CHF": "CHF ", "SEK": "kr ", "BRL": "R$",
}

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


class ExtractionError(Exception):
    """Raised when a posting cannot be fetched or read."""


def _clean_text(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = html.unescape(TAG_RE.sub(" ", value))
    value = SPACE_RE.sub(" ", value).strip()
    return value[:limit]


def _public_addresses(hostname):
    """The public addresses a host resolves to, each one listed once.

    The caller dials an address from this list rather than looking the name up
    again, so a host that answers differently the second time — the trick behind
    DNS rebinding — cannot steer the request at a private address after the
    check has passed.
    """
    if not hostname:
        raise ExtractionError("That host cannot be fetched from this app.")
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ExtractionError("Could not resolve that URL's host.")
    addresses = []
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise ExtractionError("That host cannot be fetched from this app.")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ExtractionError("That host cannot be fetched from this app.")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ExtractionError("Could not resolve that URL's host.")
    return addresses


def _is_public_host(hostname):
    try:
        _public_addresses(hostname)
    except ExtractionError:
        return False
    return True


def _connect_public(host, port, timeout, source_address=None):
    """Open a socket to an address that already passed the check."""
    problem = None
    for address in _public_addresses(host):
        try:
            return socket.create_connection((address, port), timeout, source_address)
        except OSError as error:
            problem = error
    raise ExtractionError("Could not reach that link: %s" % (problem or host))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP to the validated address instead of a second lookup of the name."""

    def connect(self):
        self.sock = _connect_public(
            self.host, self.port, self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a validated address, still presenting the real host name."""

    def connect(self):
        sock = _connect_public(
            self.host, self.port, self.timeout, self.source_address
        )
        # The certificate is checked against the name the user typed, not the
        # address that name happened to resolve to.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https") or not _is_public_host(parsed.hostname):
            raise ExtractionError("That redirect points to a blocked address.")
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


def fetch_page(url):
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ExtractionError("Enter a full http:// or https:// job posting URL.")
    if not parsed.hostname:
        raise ExtractionError("That host cannot be fetched from this app.")
    _public_addresses(parsed.hostname)
    request = urllib.request.Request(
        url.strip(),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    # The pinned handlers keep urllib from resolving the name a second time
    # inside the connection, which is where a rebinding host would get in.
    opener = urllib.request.build_opener(
        _PinnedHTTPHandler, _PinnedHTTPSHandler, _RedirectHandler
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and content_type:
                raise ExtractionError("That link is not a web page (%s)." % content_type.split(";")[0])
            raw = response.read(MAX_BYTES + 1)
            final_url = response.geturl()
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ExtractionError(
                "That site refused the request (%d). Paste the details in manually instead." % error.code
            )
        if error.code == 404:
            raise ExtractionError("That posting could not be found (404).")
        raise ExtractionError("The site returned an error (%d)." % error.code)
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise ExtractionError("Could not reach that link: %s" % reason)
    except socket.timeout:
        raise ExtractionError("That site took too long to respond.")
    except ExtractionError:
        raise
    except Exception as error:  # noqa: BLE001 - surface a readable message instead of a stack trace
        raise ExtractionError("Could not read that page: %s" % error)

    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]
    return raw.decode(charset, errors="replace"), final_url


class PageParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.meta = {}
        self.json_blocks = []
        self.title = ""
        self._in_title = False
        self._in_script = False
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attributes = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "")
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "title":
            self._in_title = True
        elif tag == "script" and "ld+json" in attributes.get("type", "").lower():
            self._in_script = True
            self._buffer = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_script:
            self._in_script = False
            block = "".join(self._buffer).strip()
            if block:
                self.json_blocks.append(block)
            self._buffer = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_script:
            self._buffer.append(data)


def _iter_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                for node in _iter_nodes(child):
                    yield node
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                for node in _iter_nodes(item):
                    yield node


def _is_job_posting(node):
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return any("jobposting" in str(item).lower() for item in node_type)
    return "jobposting" in str(node_type or "").lower()


def find_job_posting(blocks):
    for block in blocks:
        cleaned = block.strip()
        if cleaned.startswith("<!--"):
            cleaned = cleaned[4:]
        if cleaned.endswith("-->"):
            cleaned = cleaned[:-3]
        try:
            data = json.loads(cleaned)
        except (ValueError, TypeError):
            continue
        for node in _iter_nodes(data):
            if _is_job_posting(node):
                return node
    return None


def _company_name(node):
    organization = node.get("hiringOrganization") or node.get("organization")
    if isinstance(organization, dict):
        return _clean_text(organization.get("name"), 160)
    return _clean_text(organization, 160)


def _location(node):
    parts = []

    def collect(place):
        if isinstance(place, list):
            for item in place:
                collect(item)
            return
        if not isinstance(place, dict):
            if isinstance(place, str):
                parts.append(_clean_text(place, 80))
            return
        address = place.get("address")
        if isinstance(address, dict):
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                value = address.get(key)
                if isinstance(value, dict):
                    value = value.get("name")
                if value:
                    parts.append(_clean_text(value, 80))
        elif isinstance(address, str):
            parts.append(_clean_text(address, 120))
        name = place.get("name")
        if name:
            parts.append(_clean_text(name, 80))

    collect(node.get("jobLocation"))
    if not parts:
        collect(node.get("applicantLocationRequirements"))
    unique = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return ", ".join(unique)[:160]


def _salary(node):
    salary = node.get("baseSalary")
    if not salary:
        return ""
    currency = ""
    if isinstance(salary, dict):
        currency = _clean_text(salary.get("currency"), 8)
        value = salary.get("value")
    else:
        value = salary
    low = high = unit = None
    if isinstance(value, dict):
        low = value.get("minValue") or value.get("value")
        high = value.get("maxValue")
        unit = value.get("unitText")
        if not currency:
            currency = _clean_text(value.get("currency"), 8)
        if isinstance(low, dict):
            low = low.get("value")
        if isinstance(high, dict):
            high = high.get("value")
    elif value is not None:
        low = value

    def money(number):
        try:
            number = float(number)
        except (TypeError, ValueError):
            return _clean_text(number, 20)
        if number >= 1000:
            return ("%.0fk" % (number / 1000.0)).replace(".0k", "k")
        return ("%.0f" % number)

    low_text = money(low) if low is not None else ""
    high_text = money(high) if high is not None else ""
    amount = low_text
    if high_text and high_text != low_text:
        amount = (low_text + "–" + high_text) if low_text else high_text
    if not amount:
        return ""
    symbol = CURRENCY_SYMBOLS.get((currency or "").upper(), (currency + " ") if currency else "")
    suffix = ""
    unit_upper = (unit or "").upper()
    if unit_upper in ("YEAR", "ANNUAL", "YEARLY"):
        suffix = "/yr"
    elif unit_upper in ("MONTH", "MONTHLY"):
        suffix = "/mo"
    elif unit_upper in ("HOUR", "HOURLY"):
        suffix = "/hr"
    return (symbol + amount + suffix)[:100]


def _work_mode(node, location):
    job_location_type = str(node.get("jobLocationType") or "").upper()
    if "TELECOMMUTE" in job_location_type:
        return "remote"
    haystack = (location or "").lower()
    if "remote" in haystack:
        return "remote"
    if "hybrid" in haystack:
        return "hybrid"
    return "onsite"


def _source_for(final_url):
    host = (urllib.parse.urlparse(final_url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, name in SOURCE_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return name
    label = host.split(".")[0] if host else ""
    return label.replace("-", " ").title()[:100]


GENERIC_LABELS = {
    "careers", "career", "jobs", "job", "hiring", "job board", "open positions",
    "open roles", "join us", "join our team", "apply", "apply now", "home", "job application",
}

SEGMENT_RE = re.compile(r"\s+[|·/–—-]\s+")
GENERIC_HOSTS = {
    "boards", "jobs", "job", "careers", "career", "apply", "hire", "hiring", "recruiting",
    "work", "www", "app", "portal", "talent", "join", "staffing",
}


def _on_job_board(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain in SOURCE_HOSTS:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _host_label(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else ""
    return _clean_text(label.replace("-", " ").replace("_", " ").title(), 80)


def _sanitize_site_name(site_name, title):
    value = _clean_text(site_name, 160)
    if not value or value.lower() == title.lower():
        return ""
    if re.match(r"^(careers?|jobs?)\b", value, re.I):
        return ""
    value = re.sub(r"\s+(careers?|jobs?|job board|hiring)$", "", value, flags=re.I).strip()
    value = value.strip(" |–—-·")
    if value.lower() in GENERIC_LABELS or len(value) < 2:
        return ""
    return value


def _split_title(title, site_name="", final_url=""):
    """Split a page title into (role, company) using the usual separators."""
    title = _clean_text(title, 200)
    site = _sanitize_site_name(site_name, title)
    host_label = _host_label(final_url)

    for separator in (" at ", " @ "):
        if separator in title:
            left, right = title.split(separator, 1)
            left = left.strip(" |–—-·").strip()
            right = SEGMENT_RE.split(right)[0].strip()
            right = re.sub(r"\s+(careers?|jobs?)$", "", right, flags=re.I).strip()
            if left and right:
                return left[:200], right[:160]

    segments = [part.strip(" |–—-·").strip() for part in SEGMENT_RE.split(title)]
    segments = [part for part in segments if part and part.lower() not in GENERIC_LABELS]
    if not segments:
        return title, site or host_label
    if len(segments) == 1:
        return segments[0][:200], site or host_label

    role_index = max(range(len(segments)), key=lambda index: len(segments[index]))
    role = segments[role_index]
    others = [part for index, part in enumerate(segments) if index != role_index]
    host_generic = (
        not host_label
        or host_label.lower() in GENERIC_HOSTS
        or _on_job_board(final_url)
    )
    company = ""
    for candidate in (site, "" if host_generic else host_label):
        if not candidate:
            continue
        match = next(
            (part for part in others if part.lower() == candidate.lower()),
            None,
        )
        if not match:
            match = next(
                (part for part in others if part.lower().startswith(candidate.lower())),
                None,
            )
        if match:
            company = match
            break
    if not company:
        if site:
            company = site
        elif not host_generic:
            company = host_label
        elif len(others) == 1:
            company = others[0]
    return role[:200], company[:160]


def extract_job(url):
    page, final_url = fetch_page(url)
    parser = PageParser()
    try:
        parser.feed(page)
    except Exception:
        pass

    node = find_job_posting(parser.json_blocks)
    warnings = []
    fields = {}

    if node:
        fields["role"] = _clean_text(node.get("title") or node.get("name"), 200)
        if not fields["role"]:
            title = parser.meta.get("og:title") or parser.title
            fields["role"] = _split_title(
                title, parser.meta.get("og:site_name", ""), final_url
            )[0]
        fields["company"] = (
            _company_name(node)
            or _sanitize_site_name(parser.meta.get("og:site_name"), parser.title)
            or _host_label(final_url)
        )
        location = _location(node)
        fields["location"] = location
        fields["work_mode"] = _work_mode(node, location)
        description = _clean_text(node.get("description"), 6000)
        if description:
            fields["description"] = description
        salary = _salary(node)
        if salary:
            fields["salary"] = salary
        employment = node.get("employmentType")
        if employment:
            if isinstance(employment, list):
                employment = ", ".join(str(item) for item in employment)
            employment = _clean_text(employment, 60).replace("_", " ").title()
            extra = "Employment type: " + employment
            fields["description"] = (extra + "\n\n" + fields.get("description", "")).strip()[:6000]
    else:
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title
        site_name = parser.meta.get("og:site_name") or ""
        role, company_guess = _split_title(title, site_name, final_url)
        fields["role"] = role or ""
        fields["company"] = company_guess or _clean_text(site_name, 160)
        description = parser.meta.get("og:description") or parser.meta.get("description") or ""
        description = _clean_text(description, 1500)
        if len(description) > 80:
            fields["description"] = description
        warnings.append(
            "No structured job data on this page — filled what could be read from its metadata. "
            "Please review before saving."
        )

    fields["job_url"] = final_url[:1000]
    fields["source"] = _source_for(final_url)

    return {
        "fields": {key: value for key, value in fields.items() if value},
        "warnings": warnings,
        "url": final_url,
    }
