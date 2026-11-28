"""수어 자료 수집·문맥·대체 표현·전달 현황 관리 화면."""

import hashlib
import json
import os
import time
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from ksl_manager.alternatives import propose, rank_candidates, search_components
from ksl_manager.coordinate_export import (
    CoordinateUnavailable,
    download_bytes,
    locate_coordinates,
    safe_filename,
)
from ksl_manager.db import connect, rows
from ksl_manager.delivery import delivery_archive, delivery_files, json_bytes
from ksl_manager.documents import SEED_WORDS, candidates, input_rows
from ksl_manager.job_results import read_json as read_job_json
from ksl_manager.job_results import result_items, review_bundle
from ksl_manager.llm import enabled
from ksl_manager.metadata import import_metadata, search_metadata
from ksl_manager.metrics import compare_runs, export_bytes, finish_run, start_run, summary
from ksl_manager.preprocessing import job_status, launch, reprocess_job
from ksl_manager.service import (
    COLLECTION,
    PREPROCESS,
    attach_video,
    delete_term,
    get_video_defaults,
    inventory,
    register,
    review_alternative,
    save_recommendation_context,
    save_video_defaults,
    set_collection,
    update_asset,
)
from ksl_manager.sources import record_hit, search_term

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
DATA = Path(os.getenv("KSL_DATA_DIR", str(BASE / "data"))).expanduser().resolve()


def perform(fn):
    """예상 가능한 작업 오류를 사용자에게 표시한다."""
    try:
        return fn()
    except Exception as exc:
        st.error(f"작업 실패: {exc}")
        return None


def save_upload(upload):
    data = upload.getvalue()
    folder = DATA / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(upload.name).name
    path = folder / (hashlib.sha256(data).hexdigest()[:16] + "_" + name)
    path.write_bytes(data)
    return path


def term_picker(con, key):
    terms = rows(con, "SELECT * FROM terms ORDER BY word,sense")
    if not terms:
        st.info("단어 검색 화면에서 단어를 등록하세요.")
        return None
    by_id = {t["id"]: t for t in terms}
    tid = st.selectbox(
        "단어 · 의미",
        list(by_id),
        format_func=lambda i: f"{by_id[i]['word']} · {by_id[i]['sense']} (#{i})",
        key=key,
    )
    return by_id[tid]


def dashboard(con):
    m = summary(con)
    labels = [
        "전체 단어·의미",
        "의미 일치 영상 확보 단어",
        "대체 후보 단어",
        "미수집 단어",
        "검수 대기 단어",
        "좌표 처리 기록 단어",
    ]
    for col, label in zip(st.columns(3) * 2, labels):
        col.metric(label, m[label])
    st.caption(
        "표기가 같아도 의미가 다르면 별도 단어로 집계합니다. 영상 보유는 의미 일치 확인과 실제 파일 존재를 함께 확인합니다."
    )
    st.subheader("출처별 확보 파일")
    st.dataframe(
        rows(
            con,
            """SELECT source AS 출처,count(DISTINCT asset_id) AS 연결영상수 FROM term_assets GROUP BY source""",
        ),
        width="stretch",
    )
    st.subheader("최근 작업 기록")
    st.dataframe(rows(con, "SELECT * FROM events ORDER BY id DESC LIMIT 30"), width="stretch")


def coordinate_results(results, key):
    """검색 행을 선택하면 해당 원본 구간의 좌표 파일을 준비한다."""
    if not results:
        st.info("검색 결과가 없습니다.")
        return
    event = st.dataframe(
        results,
        key=key,
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        width="stretch",
        column_order=[
            "gloss",
            "start",
            "end",
            "korean_text",
            "clip_candidate",
            "record_key",
            "dataset",
            "split",
            "semantic_match",
        ],
    )
    selected = event.selection.rows
    if not selected:
        st.caption(
            "원하는 단어의 행을 선택하면 해당 구간의 좌표 JSON을 내려받을 수 있습니다. 3D를 우선합니다."
        )
        return
    row = results[selected[0]]
    st.write(f"선택: {row['gloss']} · {row['start']}~{row['end']}초")
    if not row.get("reliable"):
        st.warning("실제 시간 구간이 없어 단어 좌표를 만들 수 없습니다.")
        return
    configured = os.getenv("KSL_SOURCE_ROOTS", "").strip()
    try:
        roots_config = json.loads(configured) if configured else []
        if not isinstance(roots_config, list) or any(not isinstance(p, str) for p in roots_config):
            raise ValueError("KSL_SOURCE_ROOTS must be a JSON array of paths")
    except (ValueError, TypeError) as exc:
        st.error(f"좌표 검색 폴더 설정 오류: {exc}")
        return
    defaults = [Path(p).expanduser() for p in roots_config]
    with st.expander("좌표를 찾을 로컬 폴더"):
        locations = st.text_area(
            "검색 폴더 (한 줄에 하나)",
            "\n".join(str(p) for p in defaults if p.is_dir()),
            key=key + "_roots",
        )
    roots = tuple(line.strip() for line in locations.splitlines() if line.strip())
    signature = (row["id"], row.get("gloss"), row.get("start"), row.get("end"), roots, "3d_then_2d")
    cached = st.session_state.get(key + "_coordinate_result")
    retry = st.button("좌표 다시 찾기", key=key + "_retry")
    if not cached or cached["signature"] != signature or retry:
        with st.spinner("선택한 단어의 좌표를 확인하고 있습니다. 3D를 먼저 찾습니다…"):
            try:
                data = locate_coordinates(row, roots)
                cached = {
                    "signature": signature,
                    "data": download_bytes(data),
                    "frames": data["metadata"]["frame_count"],
                    "dimension": "2d"
                    if data["metadata"].get("coordinate_type") == "original_2d"
                    else "3d",
                }
            except CoordinateUnavailable as exc:
                cached = {"signature": signature, "error": str(exc)}
        st.session_state[key + "_coordinate_result"] = cached
    if "error" in cached:
        st.warning(cached["error"])
        st.caption(
            "글로스가 있다는 것과 해당 구간의 좌표를 보유한 것은 다릅니다. 2D 좌표를 3D로 바꾸거나 다른 단어의 구간을 대신 내려받지 않습니다."
        )
        return
    dimension = cached["dimension"]
    label = dimension.upper()
    if dimension == "3d":
        st.success(f"원본 3D 좌표 확인 · {cached['frames']}프레임")
    else:
        st.info(f"이 구간은 보유 3D가 없어 2D 좌표를 제공합니다 · {cached['frames']}프레임")
        st.caption(
            "2D의 세 번째 값은 신뢰도이며 깊이(Z)가 아닙니다. 3D 아바타 적용에는 구현팀의 별도 처리가 필요합니다."
        )
    st.download_button(
        f"선택한 단어 {label} JSON 저장",
        cached["data"],
        file_name=safe_filename(row["gloss"], dimension),
        mime="application/json",
        key=key + "_download",
        on_click="ignore",
    )


def search_page(con):
    with st.form("register"):
        word = st.text_area("단어 (여러 개는 줄바꿈 또는 쉼표)", height=90)
        sense = st.text_input("문맥상 의미 (미입력 시 미분류)")
        sentence = st.text_area("원본 문장 · 문맥")
        source = st.text_input("문서 출처")
        domain = st.selectbox("주제", ["산업안전", "공공기관·의료", "공통"])
        if st.form_submit_button("단어 등록"):
            result = perform(
                lambda: [
                    register(con, w, sense, sentence, source, domain)
                    for w in word.replace(",", "\n").splitlines()
                    if w.strip()
                ]
            )
            if result is not None:
                st.success(f"{len(result)}건 처리 · 기존 단어 {sum(r[1] for r in result)}건 재사용")
    with st.expander("문서·단어 파일 가져오기"):
        upload = st.file_uploader("CSV / XLSX / TXT / PDF", type=["csv", "xlsx", "txt", "pdf"])
        manual = st.text_area("매뉴얼 텍스트")
        doc_source = st.text_input("매뉴얼 출처")
        use_llm = st.checkbox(
            "문서 핵심어 AI 추출 (입력 구간을 API로 전송)", disabled=not enabled()
        )
        if st.button("입력 후보 읽기"):

            def read():
                result = input_rows(upload.name, upload.getvalue()) if upload else []
                texts = [r for r in result if not r["word"]]
                terms = [r for r in result if r["word"]]
                for r in texts:
                    terms.extend(candidates(con, r["sentence"], r["source"], use_llm))
                if manual:
                    terms.extend(candidates(con, manual, doc_source, use_llm))
                return terms

            st.session_state["input_candidates"] = perform(read) or []
            st.session_state["candidate_revision"] = (
                st.session_state.get("candidate_revision", 0) + 1
            )
        candidate_rows = st.session_state.get("input_candidates", [])
        if candidate_rows:
            frame = pd.DataFrame(candidate_rows)
            frame.insert(0, "등록", False)
            edited = st.data_editor(
                frame,
                key=f"candidate_editor_{st.session_state.get('candidate_revision', 0)}",
                column_config={"등록": st.column_config.CheckboxColumn("등록", default=False)},
                hide_index=True,
                width="stretch",
            )
            selected = edited.loc[edited["등록"].fillna(False)].to_dict("records")
            st.caption(f"등록할 후보만 체크하세요. 선택 {len(selected)}개 / 전체 {len(frame)}개")
            if st.button("선택한 후보 등록", disabled=not selected):

                def add():
                    if any(not str(r.get("word") or "").strip() for r in selected):
                        raise ValueError("선택한 후보의 단어가 비어 있습니다.")
                    for r in selected:
                        register(
                            con,
                            r["word"],
                            r.get("sense", ""),
                            r.get("sentence", ""),
                            r.get("source", ""),
                            domain,
                        )
                    st.success(f"선택한 후보 {len(selected)}개를 등록했습니다.")

                perform(add)
        st.caption(
            "키가 없으면 등록어와 초기 20단어를 문자 대조합니다. PDF가 스캔 이미지이면 텍스트가 없어 OCR이 별도로 필요합니다."
        )
    if st.button("초기 테스트 20단어 등록 (검색 결과는 미확인)"):
        for w in SEED_WORDS:
            register(con, w, "전하를 띠는 현상" if w == "대전" else "", domain="산업안전")
        st.success("초기 단어를 등록했습니다.")
    term = term_picker(con, "search_term")
    if not term:
        return
    with st.form("append_context"):
        extra_sentence = st.text_area("선택 단어의 원본 문장 추가")
        extra_source = st.text_input("추가 문장 출처")
        if st.form_submit_button("문맥 저장"):
            if extra_sentence.strip():
                register(
                    con, term["word"], term["sense"], extra_sentence, extra_source, term["domain"]
                )
                st.success("문맥을 저장했습니다.")
            else:
                st.error("원본 문장을 입력하세요.")
    st.dataframe(
        rows(con, "SELECT sentence,document_source FROM contexts WHERE term_id=?", (term["id"],)),
        width="stretch",
    )
    if st.button("보유 메타데이터 검색 · 공식 검색 링크"):
        result = perform(lambda: search_term(con, term["id"]))
        st.session_state["search_result"] = {"term_id": term["id"], "result": result}
    stored = st.session_state.get("search_result", {})
    if stored.get("term_id") == term["id"] and stored.get("result"):
        r = stored["result"]
        coordinate_results(r["metadata"], f"search_results_{term['id']}")
        for link in r["links"]:
            st.link_button(link["source"], link["url"])
            st.caption(f"검색어: {link['query']} · {link['conditions']}")
        st.info("외부 링크는 검색 결과가 아닙니다. 직접 확인한 표제어를 아래에 기록하세요.")
    with st.expander("직접 확인한 외부 검색 결과 기록"):
        with st.form("hit"):
            hit_source = st.text_input("자료 출처")
            page = st.text_input("실제 원본 페이지 HTTPS 주소")
            head = st.text_input("발견한 표제어")
            video = st.text_input("실제로 확인한 영상 주소 (선택)")
            conditions = st.text_input("영상별 이용 조건 확인 기록")
            match = st.selectbox("문맥·의미 일치", ["미확인", "의미 일치 확인", "의미 불일치"])
            if st.form_submit_button("확인 결과 저장"):
                perform(
                    lambda: record_hit(
                        con, term["id"], hit_source, page, head, video, match, conditions
                    )
                )


def alternative_page(con):
    term = term_picker(con, "alternative_term")
    if not term:
        return
    st.caption(
        "AI 추천 후보 — 검수 필요. 후보 순위는 전문가 승인 여부 → 신규 수집 수 → 구성 수 순입니다. 의미 보존 점수나 번역 정확도를 뜻하지 않습니다."
    )
    contexts = rows(
        con,
        "SELECT sentence,document_source FROM contexts WHERE term_id=? ORDER BY id DESC",
        (term["id"],),
    )
    current = next((r for r in contexts if r["sentence"].strip()), {})
    missing = term["sense"].startswith("미분류") or not current
    with st.expander("원본 문장·의미 입력", expanded=missing):
        with st.form(f"recommendation_context_{term['id']}"):
            meaning = st.text_input(
                "이 문장에서 단어가 뜻하는 의미",
                value="" if term["sense"].startswith("미분류") else term["sense"],
                placeholder="예: 온도가 낮은 상태",
            )
            sentence = st.text_area(
                "대체 표현에 사용할 원본 문장",
                value=current.get("sentence", ""),
                placeholder="이 단어가 사용된 매뉴얼의 원문을 넣으세요.",
            )
            source = st.text_input("원본 문서 출처", value=current.get("document_source", ""))
            if st.form_submit_button("문맥 저장"):
                saved = perform(
                    lambda: save_recommendation_context(con, term["id"], meaning, sentence, source)
                )
                if saved:
                    st.success("문맥을 저장했습니다. 아래에서 대체 표현 후보를 추천받으세요.")
    updated = con.execute("SELECT sense FROM terms WHERE id=?", (term["id"],)).fetchone()
    ready = not updated["sense"].startswith("미분류") and bool(
        con.execute(
            "SELECT 1 FROM contexts WHERE term_id=? AND trim(sentence)<>''", (term["id"],)
        ).fetchone()
    )
    if not ready:
        st.info("위에서 원본 문장과 문맥상 의미를 저장한 뒤 추천을 실행하세요.")
    use_search = st.checkbox("추천 직후 미확보 구성 단어를 검색", value=True)
    max_search = st.number_input("추가 단어 검색 한도", 1, 50, 12)
    depth = st.number_input("구성 단어 탐색 깊이", 1, 3, 1)
    consent = st.checkbox("단어·원문 문맥·보유 자료 목록을 LLM API에 전송하는 데 동의")
    if st.button("대체 표현 후보 추천", disabled=not enabled() or not consent or not ready):

        def recommend():
            ids = propose(con, term["id"])
            remaining = int(max_search)
            if use_search:
                for aid in ids:
                    if remaining <= 0:
                        break
                    remaining -= len(search_components(con, aid, int(depth), remaining))
            return ids

        result = perform(recommend)
        if result is not None:
            st.success(f"후보 {len(result)}개 저장")
    if not enabled():
        st.info(
            "LLM 키 미설정: 기본 검색은 사용할 수 있습니다. 추천 기능은 키 설정 후 사용할 수 있습니다."
        )
    for r in rank_candidates(con, term["id"]):
        with st.expander(f"#{r['id']} {r['expression']} · {r['review_status']}", expanded=True):
            for label, key in [
                ("추천 이유", "reason"),
                ("의미 차이", "difference"),
                ("의미 손실 위험", "risk"),
                ("판단 근거 (AI 진술, 미검증)", "evidence"),
            ]:
                st.write(label + ": " + r[key])
            st.dataframe(r["components"], width="stretch")
            if st.button("이 후보의 미확보 구성 단어 재검색", key=f"search_parts_{r['id']}"):
                found = perform(
                    lambda: search_components(con, r["id"], int(depth), int(max_search))
                )
                if found is not None:
                    st.success(f"{len(found)}개 구성 단어 검색")
            with st.form(f"review_{r['id']}"):
                reviewer = st.text_input("검수자 이름")
                role = st.selectbox(
                    "검수자 역할", ["일반 검토자", "농인 검수자", "한국수어 전문가"]
                )
                decision = st.selectbox(
                    "검수 결과", ["검수 대기", "전문가 승인", "수정 필요", "사용 불가"]
                )
                note = st.text_area("검토 근거 · 수정 의견")
                expression = st.text_input("표현 수정 (선택; 수정 후에는 재검수 필요)")
                if st.form_submit_button("검수 기록 저장"):
                    outcome = perform(
                        lambda: review_alternative(
                            con, r["id"], decision, reviewer, role, note, expression
                        )
                    )
                    if outcome is not None:
                        st.success(f"검수 저장 완료: {outcome}")
    st.caption(
        "전문가 역할은 검수자가 직접 선언하는 기록입니다. 계정 인증·자격 검증 기능은 없습니다."
    )


def manage_page(con):
    data = inventory(con)
    filters = st.multiselect("수집 상태 필터", COLLECTION)
    domains = st.multiselect("주제 필터", ["산업안전", "공공기관·의료", "공통"])
    source = st.text_input("출처 필터")
    filtered = [
        r
        for r in data
        if (not filters or r["collection_status"] in filters)
        and (not domains or set((r["domains"] or r["domain"]).split(",")) & set(domains))
        and (not source or source in (r["sources"] or ""))
    ]
    st.dataframe(filtered, width="stretch")
    term = term_picker(con, "manage_term")
    if not term:
        return
    with st.form("term_edit"):
        meaning = st.text_input("의미 수정", term["sense"])
        state = st.selectbox(
            "수집 상태", COLLECTION, index=COLLECTION.index(term["collection_status"])
        )
        if st.form_submit_button("단어 상태 저장"):

            def update():
                if not meaning.strip():
                    raise ValueError("의미를 입력하세요.")
                with con:
                    con.execute(
                        "UPDATE terms SET sense=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (meaning, term["id"]),
                    )
                    set_collection(con, term["id"], state)
                st.success("저장했습니다.")

            perform(update)
    defaults = get_video_defaults(con)
    with st.expander("공통 출처 설정 (선택 · 한 번 저장하면 계속 사용)"):
        with st.form("video_defaults"):
            common_source = st.text_input(
                "공통 영상 출처",
                value=defaults["source"],
                placeholder="예: 국립국어원 한국수어누리사전",
            )
            common_license = st.text_input(
                "공통 이용조건 기록",
                value=defaults["license_note"],
                placeholder="모르면 비워두세요",
            )
            if st.form_submit_button("공통 설정 저장"):
                save_video_defaults(con, common_source, common_license)
                st.success("저장했습니다. 이후 등록하는 영상에 기본 적용됩니다.")
                st.rerun()
    with st.form(f"video_add_{term['id']}"):
        path = st.text_input("로컬 원본 영상 경로")
        upload = st.file_uploader("또는 영상 파일 업로드", type=["mp4", "mov", "webm", "m4v"])
        with st.expander("출처·글로스 세부정보 (선택)"):
            src = st.text_input("영상 출처", value=defaults["source"])
            url = st.text_input("원본 페이지 URL")
            license_note = st.text_input("이용 조건 확인 기록", value=defaults["license_note"])
            gloss = st.text_input("수어 글로스")
            st.caption("입력하지 않은 출처·이용조건은 미확인으로 기록합니다.")
        match = st.checkbox("원문 의미와 영상 표현 일치를 직접 확인했습니다")
        if st.form_submit_button("영상 보관 · 해시 중복 확인"):

            def add():
                uploaded_path = save_upload(upload) if upload else None
                result = attach_video(
                    con,
                    term["id"],
                    uploaded_path or path,
                    DATA,
                    src,
                    license_note,
                    url,
                    gloss,
                    match,
                )
                if uploaded_path:
                    uploaded_path.unlink()
                st.success("보관 완료 · 기존 영상 재사용" if result[1] else "보관 완료")

            perform(add)
    for a in rows(
        con,
        """SELECT ta.*,a.local_path FROM term_assets ta JOIN assets a ON a.id=ta.asset_id WHERE ta.term_id=?""",
        (term["id"],),
    ):
        with st.expander(f"영상 #{a['id']} · {a['source']} · {a['preprocessing_status']}"):
            if Path(a["local_path"]).is_file():
                st.video(a["local_path"])
            else:
                st.warning("보관 경로에 영상이 없습니다.")
            st.code(a["local_path"])
            with st.form(f"asset_{a['id']}"):
                prep = st.selectbox(
                    "전처리 상태", PREPROCESS, index=PREPROCESS.index(a["preprocessing_status"])
                )
                coordinate = st.text_input("좌표 파일 경로", a["coordinate_path"] or "")
                match_status = st.selectbox(
                    "의미 검수",
                    ["미확인", "의미 일치 확인", "의미 불일치"],
                    index=["미확인", "의미 일치 확인", "의미 불일치"].index(a["match_review"]),
                )
                if st.form_submit_button("영상 처리 상태 저장"):
                    perform(lambda: update_asset(con, a["id"], prep, coordinate, match_status))
            with st.expander("기존 좌표 전처리 연결"):
                pipeline = st.text_input("run.py가 있는 폴더", key=f"pipe_{a['id']}")
                python = st.text_input(
                    "파이프라인 Python 경로 (빈칸이면 해당 .venv)", key=f"python_{a['id']}"
                )
                hand_gap = st.number_input(
                    "양손 선형보간 최대 누락(초)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.4,
                    step=0.05,
                    key=f"hand_gap_{a['id']}",
                )
                st.caption(
                    "양손 각각의 짧은 누락만 앞뒤 검출 좌표로 보간합니다. 시작·끝 누락과 한도를 넘는 누락은 남겨둡니다."
                )
                if st.button("별도 작업 폴더에서 실행", key=f"launch_{a['id']}"):
                    job = perform(
                        lambda: launch(
                            a["local_path"],
                            pipeline,
                            DATA,
                            python or None,
                            display_name=term["word"],
                            hand_max_gap_sec=hand_gap,
                        )
                    )
                    if job:
                        st.success(f"실행 요청: {job}")
    with st.expander("단어 등록 삭제"):
        confirm = st.checkbox("이 단어의 문맥·검색·후보 기록 삭제 확인 (보관 영상은 유지)")
        if st.button("단어 등록 삭제", disabled=not confirm):
            delete_term(con, term["id"])
            st.rerun()


def metadata_page(con):
    dataset = st.text_input("데이터셋 출처", "AI Hub 사용자 보유 메타데이터")
    split = st.selectbox("학습 분할", ["미확인", "Training", "Validation", "Test"])
    uploads = st.file_uploader(
        "메타데이터 여러 개",
        type=["json", "jsonl", "csv", "xlsx", "txt"],
        accept_multiple_files=True,
    )
    folder = st.text_input("또는 로컬 메타데이터 폴더 경로")
    if st.button("메타데이터 인덱싱"):

        def index():
            paths = [save_upload(u) for u in uploads]
            if folder:
                root = Path(folder).expanduser()
                if not root.is_dir():
                    raise ValueError("폴더를 찾지 못했습니다.")
                paths.extend(
                    p
                    for p in root.rglob("*")
                    if p.is_file()
                    and p.suffix.lower() in [".json", ".jsonl", ".csv", ".xlsx", ".txt"]
                )
            count = 0
            progress = st.empty()
            for i, path in enumerate(paths):
                progress.info(f"{i + 1}/{len(paths)} 파일: {path.name}")
                count += import_metadata(
                    con,
                    path,
                    dataset,
                    split,
                    progress=lambda n: progress.info(f"{path.name}: {n:,}행"),
                )
            progress.success(f"인덱싱 완료: 새 레코드 {count:,}개")

        perform(index)
    query = st.text_input("문장 · 글로스 검색")
    if query:
        coordinate_results(
            search_metadata(con, query),
            "metadata_results_" + hashlib.sha256(query.encode()).hexdigest()[:12],
        )
    st.caption(
        "clip_candidate는 글로스 표기와 원본 타임스탬프가 있는 구간입니다. 의미 일치·영상 보유를 보장하지 않습니다. 분할은 학습/평가 시 별도로 지키세요."
    )


@st.cache_data(show_spinner=False, max_entries=8)
def paired_delivery(avatar_bytes, word):
    word, files = delivery_files(json.loads(avatar_bytes), word)
    return word, files, delivery_archive(files)


def job_result_panel(con, job):
    status = job_status(job)
    manifest = read_job_json(job / "job.json") if (job / "job.json").is_file() else {}
    display_name = manifest.get("display_name")
    inputs = list((job / "input").glob("*"))
    if not display_name and inputs:
        source_hash = inputs[0].stem
        match = con.execute(
            """SELECT t.word FROM terms t JOIN term_assets ta ON ta.term_id=t.id
            JOIN assets a ON a.id=ta.asset_id WHERE a.sha256=? ORDER BY t.id LIMIT 1""",
            (source_hash,),
        ).fetchone()
        display_name = match[0] if match else inputs[0].stem
    display_name = display_name or "영상"
    with st.expander(f"{display_name} · {status['state']} · {job.name}", expanded=True):
        items = result_items(job)
        if not items:
            st.info("결과가 아직 없거나 처리가 실패했습니다. 아래 실행 기록을 확인하세요.")
        for index, item in enumerate(items):
            label = display_name if len(items) == 1 else f"{display_name}_{index + 1}"
            label = label.replace("/", "_").replace("\\", "_")
            st.download_button(
                "후처리 좌표 JSON 받기",
                item["avatar"].read_bytes(),
                file_name=label + "_avatar.json",
                mime="application/json",
                key=f"avatar_{job.name}_{index}",
            )
            st.download_button(
                "수동 보정용 영상·좌표 ZIP 받기",
                review_bundle(item, label),
                file_name=label + "_검수용.zip",
                mime="application/zip",
                key=f"review_{job.name}_{index}",
            )
            st.caption(
                "avatar JSON은 추출·자동 후처리가 끝난 보정 입력 파일입니다. 검수·최종 승인된 전달본과는 구분하세요."
            )
            if status["state"] == "완료":
                with st.expander("좌표·재생상태 내보내기", expanded=True):
                    pair = perform(lambda: paired_delivery(item["avatar"].read_bytes(), label))
                    if pair:
                        word, files, archive = pair
                        st.caption(
                            "검수 전 연동 확인용입니다. 재생상태는 아래 AI Hub 계열 3D 좌표와 한 쌍이며, ZIP에 좌표와 재생상태가 함께 들어 있습니다."
                        )
                        st.download_button(
                            "AI Hub 계열 3D 좌표 JSON 받기",
                            json_bytes(files[f"좌표_3D/{word}.json"]),
                            file_name=word + "_3D.json",
                            mime="application/json",
                            key=f"paired_3d_{job.name}_{index}",
                        )
                        st.download_button(
                            "재생상태 JSON 받기",
                            json_bytes(files[f"재생상태/{word}.json"]),
                            file_name=word + "_재생상태.json",
                            mime="application/json",
                            key=f"paired_state_{job.name}_{index}",
                        )
                        st.download_button(
                            "좌표·재생상태 묶음 ZIP 받기",
                            archive,
                            file_name=word + "_좌표_재생상태.zip",
                            mime="application/zip",
                            key=f"paired_zip_{job.name}_{index}",
                        )
            interpolation = item["report_data"].get("hand_interpolation")
            if interpolation:
                st.caption(
                    f"양손 선형보간: 최대 {interpolation['max_gap_seconds']}초 · {interpolation['max_gap_frames']}프레임. 보간점은 point_source=2로 기록합니다."
                )
                hand_rows = []
                for side, title in [("left_hand", "왼손"), ("right_hand", "오른손")]:
                    part = item["report_data"].get("parts", {}).get(side, {})
                    hand_rows.append(
                        {
                            "부위": title,
                            "보간 적용 프레임": part.get("interpolated_frames", 0),
                            "남은 누락 프레임": sum(
                                e - s
                                for s, e in part.get(
                                    "remaining_missing_frame_runs_end_exclusive", []
                                )
                            ),
                        }
                    )
                st.dataframe(hand_rows, hide_index=True, width="stretch")
            if item["check"].is_file():
                st.write("자동 후처리 비교 영상")
                st.video(str(item["check"]))
            st.write("우선 확인할 구간")
            if item["events"]:
                st.dataframe(item["events"], hide_index=True, width="stretch")
                st.caption(
                    "자동 검출 후보입니다. 실제 손 겹침이나 빠른 동작도 잡힐 수 있으므로 모두 수동 보정할 필요는 없습니다."
                )
            else:
                st.info("자동 규칙에서 잡힌 구간이 없습니다. 전체 영상은 한 번 확인하세요.")
            n = int(item["audit_data"].get("frames") or 0)
            face_gaps = (
                item["report_data"]
                .get("parts", {})
                .get("face", {})
                .get("remaining_missing_frame_runs_end_exclusive", [])
            )
            if n and sum(e - s for s, e in face_gaps) >= n:
                st.warning(
                    "얼굴 좌표가 전체 구간에서 누락돼 있습니다. 표정 등 비수지 표현이 필요한지 확인하고, 필요하면 재추출을 검토하세요."
                )
            st.caption(
                "영상과 점이 맞고 동작이 이어지면 그대로 검수할 수 있습니다. 한 손에 양손이 붙거나, 손이 바뀌거나, 보이는 손의 점이 사라지는 등 실제 오류가 남으면 해당 구간만 수동 보정하세요."
            )
            st.write("결과 폴더")
            st.code(str(item["avatar"].parent))
        if status["state"] == "완료" and items:
            gap = st.number_input(
                "양손 보간 한도(초)",
                min_value=0.0,
                max_value=1.0,
                value=float(manifest["hand_max_gap_sec"])
                if manifest.get("hand_max_gap_sec") is not None
                else 0.4,
                step=0.05,
                key=f"reprocess_gap_{job.name}",
            )
            if st.button("추출 없이 양손 보간 다시 적용", key=f"reprocess_{job.name}"):
                new_job = perform(lambda: reprocess_job(job, gap, display_name))
                if new_job:
                    st.success(
                        f"새 후처리 작업을 시작했습니다: {new_job.name}. 작업 상태 새로고침으로 결과를 확인하세요."
                    )
        with st.expander("실행 기록"):
            if (job / "run.log").is_file():
                st.code((job / "run.log").read_text(errors="replace", encoding="utf-8")[-4000:])


def reports_page(con):
    st.json(summary(con), expanded=True)
    records = inventory(con)
    st.download_button("단어 현황 CSV", export_bytes(records), "수어_자료현황.csv", "text/csv")
    st.download_button("단어 현황 Excel", export_bytes(records, "xlsx"), "수어_자료현황.xlsx")
    st.caption(
        "수동/자동 비교는 같은 단어 집합과 같은 검색 조건을 사용하세요. 사용자 개입 시간은 직접 측정해 기록합니다."
    )
    with st.form("benchmark"):
        mode = st.selectbox("측정 방식", ["수동", "자동"])
        cohort = st.text_input("실험명")
        conditions = st.text_input("검색 조건 (비교할 때 동일하게 입력)")
        ids = st.multiselect("측정 대상 단어 ID", [r["id"] for r in records])
        elapsed = st.number_input("실측 전체 소요 시간(초)", min_value=0.0)
        active_measured = st.checkbox("사용자 개입 시간도 측정함")
        active = st.number_input("실측 사용자 개입 시간(초)", min_value=0.0)
        source_count = st.number_input("실제 검색한 출처 수", min_value=0)
        if st.form_submit_button("측정 기록 저장"):

            def measured():
                if active_measured and active > elapsed:
                    raise ValueError("개입 시간은 전체 시간 이하여야 합니다.")
                rid = start_run(con, mode, ids, cohort, conditions)
                finish_run(
                    con, rid, elapsed, active if active_measured else None, int(source_count)
                )
                st.success("실측값을 저장했습니다.")

            perform(measured)
    if st.button("등록 전체 단어 로컬 자동검색 시간 측정", disabled=not records):

        def bench():
            tids = [r["id"] for r in records]
            rid = start_run(
                con, "자동", tids, "로컬 메타데이터 검색", "로컬 메타데이터만; 외부 링크 제외"
            )
            started = time.perf_counter()
            for tid in tids:
                search_term(con, tid, rid)
            finish_run(
                con,
                rid,
                time.perf_counter() - started,
                None,
                1 if con.execute("SELECT count(*) FROM metadata_records").fetchone()[0] else 0,
            )
            st.success("측정 완료")

        perform(bench)
    runs = rows(con, "SELECT * FROM runs ORDER BY id DESC")
    st.dataframe(runs, width="stretch")
    if len(runs) >= 2:
        a = st.selectbox("비교 실행 A", [r["id"] for r in runs])
        b = st.selectbox("비교 실행 B", [r["id"] for r in runs], index=1)
        if st.button("동일 조건 비교"):
            result = perform(lambda: compare_runs(con, a, b))
            if result:
                st.dataframe(result, width="stretch")
    jobs = DATA / "jobs"
    if jobs.exists():
        st.subheader("전처리 결과 · 영상 검수")
        if st.button("작업 상태 새로고침"):
            st.rerun()
        for job in sorted((p for p in jobs.iterdir() if p.is_dir()), reverse=True)[:10]:
            perform(lambda: job_result_panel(con, job))


def main():
    st.set_page_config(page_title="이음 · 수어 자료 관리", layout="wide")
    st.title("이음 · 수어 자료 관리")
    st.caption("단어의 의미, 확보한 영상, 대체 표현 검수와 전달 기록을 함께 관리합니다.")
    pages = {
        "대시보드": dashboard,
        "단어 검색": search_page,
        "대체 표현": alternative_page,
        "데이터 관리": manage_page,
        "메타데이터": metadata_page,
        "통계·작업": reports_page,
    }
    page = st.sidebar.radio("메뉴", list(pages))
    st.sidebar.caption("LLM 연결 가능" if enabled() else "LLM 설정 없음")
    con = connect(os.getenv("KSL_DB", str(DATA / "signs.sqlite3")))
    try:
        pages[page](con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
