"""Fictional raw EDINET/JPX documents for the packaged offline scoring walkthrough."""

import io
import zipfile
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from atlas_terminal.edinet_history import EdinetHistory
from atlas_terminal.errors import AtlasError
from atlas_terminal.japan_company_sources import JPX_LIST, edinet_list_url
from atlas_terminal.providers import Snapshot
from atlas_terminal.util import canonical

CAPTURE = "2026-11-07T12:00:00Z"
RESOURCE = files("atlas_terminal").joinpath("resources/research/edinet_scoring")


def archive_xml(body: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("XBRL/PublicDoc/fictional.xbrl", (2026, 1, 1, 0, 0, 0))
        archive.writestr(info, body)
    return output.getvalue()


class EdinetScoringFixtures:
    synthetic = True

    def get(self, url: str, ttl: int) -> Snapshot:
        if url == JPX_LIST:
            body, name = RESOURCE.joinpath("companies.xlsx").read_bytes(), "companies.xlsx"
        elif ".json?" in url:
            day = parse_qs(urlsplit(url).query)["date"][0]
            year = int(day[:4])
            records = []
            if 2022 <= year <= 2026 and day[4:] == "-06-26":
                for code in ("9999", "9998", "9997", "9996"):
                    records.append(
                        {
                            "docID": f"S{code}{year % 100:02}A",
                            "edinetCode": "E9" + code,
                            "secCode": code + "0",
                            "filerName": "FICTIONAL Atlas " + code,
                            "docTypeCode": "120",
                            "docDescription": "FICTIONAL annual report",
                            "periodStart": f"{year - 1}-04-01",
                            "periodEnd": f"{year}-03-31",
                            "submitDateTime": day + " 09:30",
                            "xbrlFlag": "1",
                            "withdrawalStatus": "0",
                            "disclosureStatus": "0",
                            "parentDocID": None,
                        }
                    )
            body, name = (
                canonical({"metadata": {"status": "200"}, "results": records}),
                day + ".json",
            )
        else:
            document = urlsplit(url).path.rsplit("/", 1)[-1]
            if (
                len(document) != 8
                or document[1:5] not in {"9999", "9998", "9997", "9996"}
                or document[-1] != "A"
            ):
                raise AtlasError("SOURCE_UNAVAILABLE", "No such fictional EDINET report.")
            code, year = document[1:5], 2000 + int(document[5:7])
            template = "ifrs.xbrl" if code == "9998" else "jgaap.xbrl"
            xml = RESOURCE.joinpath(template).read_text()
            for key, value in {
                "YEAR": str(year),
                "PREVIOUS": str(year - 1),
                "CODE": code,
                "EDINET": "E9" + code,
            }.items():
                xml = xml.replace("__" + key + "__", value)
            body, name = archive_xml(xml.encode()), document + ".zip"
        return Snapshot(body, "https://fixtures.atlas.invalid/edinet-scoring/" + name, CAPTURE)

    def close(self) -> None:
        pass


def seed_history(history: EdinetHistory) -> None:
    transport = EdinetScoringFixtures()
    for year in range(2022, 2027):
        day = f"{year}-06-26"
        history.ingest(day, transport.get(edinet_list_url(day), 21600))
