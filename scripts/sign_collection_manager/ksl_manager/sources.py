"""공식 검색 화면 연결과 사용자가 확인한 검색 결과의 기록.

검색 링크는 발견으로 집계하지 않는다. 비공개 API와 미확인 다운로드를 호출하지 않는다.
"""

import time
from urllib.parse import urlencode, urlsplit

from .metadata import search_metadata

SOURCES = [
    (
        "한국수어사전",
        "https://sldict.korean.go.kr/front/search/searchSignCteList.do",
        "영상별 권리 확인; 자동 다운로드 비활성",
    ),
    (
        "한국수어누리사전",
        "https://ksldict.korean.go.kr/kslserv/search/korean.do",
        "한국어 대응표현에 검색어 입력; 공개 검색 API 미확인",
    ),
    (
        "AI Hub 재난안전",
        "https://www.aihub.or.kr/aihubdata/data/view.do?currMenu=115&dataSetSn=636&topMenu=100",
        "로그인·승인·다운로드 조건 확인; 보유 메타데이터 우선",
    ),
    (
        "AI Hub 수어영상",
        "https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=103",
        "데이터 신청 및 이용 조건 확인",
    ),
    (
        "국립국어원 말뭉치",
        "https://kli.korean.go.kr/main/requestMain.do",
        "말뭉치 신청·약정 필요; 보유 파일 가져오기",
    ),
    (
        "공공기관 수어안내",
        "https://lst.go.kr/comm/cardDetail.do?bno=8749",
        "연명의료 제도 수어 안내; 단어 존재·영상 재사용 권리 미확인",
    ),
]


def search_links(word):
    result = []
    for name, url, note in SOURCES:
        if name == "한국수어사전":
            url += "?" + urlencode({"searchKeyword": word, "search_gubun": "cte", "pageIndex": 1})
        result.append(
            {"source": name, "url": url, "query": word, "conditions": note, "outcome": "검색 링크"}
        )
    return result


def search_term(con, tid, run_id=None):
    term = con.execute("SELECT * FROM terms WHERE id=?", (tid,)).fetchone()
    if not term:
        raise ValueError("단어가 없습니다.")
    started = time.perf_counter()
    results = search_metadata(con, term["word"])
    links = search_links(term["word"])
    with con:
        for link in links:
            con.execute(
                """INSERT INTO searches(term_id,source,query,page_url,outcome,conditions,run_id)
                         VALUES (?,?,?,?,?,?,?)""",
                (
                    tid,
                    link["source"],
                    term["word"],
                    link["url"],
                    "검색 링크",
                    link["conditions"],
                    run_id,
                ),
            )
        for r in results:
            con.execute(
                """INSERT INTO searches(term_id,source,query,page_url,found_headword,outcome,conditions,run_id)
                         VALUES (?,?,?,?,?,?,?,?)""",
                (
                    tid,
                    r["dataset"],
                    term["word"],
                    "",
                    r.get("gloss"),
                    "발견",
                    "로컬 메타데이터 검색 결과. 의미·영상 확보 미확인.",
                    run_id,
                ),
            )
        if term["collection_status"] not in ["영상 확보 완료", "대체 표현 후보 발견"]:
            con.execute(
                "UPDATE terms SET collection_status=? WHERE id=?",
                ("검색 결과 발견" if results else "검색 중", tid),
            )
    return {
        "metadata": results,
        "links": links,
        "elapsed_seconds": time.perf_counter() - started,
        "searched_source_count": 1
        if con.execute("SELECT count(*) FROM metadata_records").fetchone()[0]
        else 0,
        "note": "공식 링크를 생성한 출처는 실제 검색 소요 시간·출처 수에 포함하지 않습니다.",
    }


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("인증 정보 없는 HTTPS 주소를 입력하세요.")
    return url


def record_hit(
    con, tid, source, page_url, headword, video_url="", semantic_match="미확인", conditions=""
):
    validate_url(page_url)
    if video_url:
        validate_url(video_url)
    if not headword.strip() or not conditions.strip():
        raise ValueError("실제 표제어와 이용 조건 확인 기록을 입력하세요.")
    with con:
        con.execute(
            """INSERT INTO searches(term_id,source,query,page_url,found_headword,video_url,outcome,semantic_match,conditions)
                     VALUES (?,?,?,?,?,?,'발견',?,?)""",
            (
                tid,
                source,
                con.execute("SELECT word FROM terms WHERE id=?", (tid,)).fetchone()[0],
                page_url,
                headword,
                video_url,
                semantic_match,
                conditions,
            ),
        )
        con.execute(
            "UPDATE terms SET collection_status='검색 결과 발견' WHERE id=? AND collection_status NOT IN ('영상 확보 완료','대체 표현 후보 발견')",
            (tid,),
        )
