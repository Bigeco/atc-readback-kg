# 미국 ATC 무선교신 기반 Knowledge Graph 구축: 문제정의 및 구축 방안 (v2)

> Team 6 · 2026-10-14 · 기존 "ver_A" 기획서를 **미국 교신 / 음성 데이터 / 데모 중심**으로 좁혀 재정리한 버전
> 근거 자료: 팀 공유 샘플(`미국교신샘플.mp3`, 248초)을 Whisper large-v3로 직접 전사·분석한 결과 (`stt_sample/`)

---

## 0. 기존 기획서(ver_A)에서 바뀐 점 요약

| 항목 | ver_A | v2 (이번) | 이유 |
|---|---|---|---|
| 대상 | 한국 (한/영 혼용, 군민공용) | **미국 (FAA 관제, 영어)** | 시간 제약, 공개 음성 확보 가능성 |
| 입력 | 텍스트 스크립트 전제 | **음성 → STT 포함** | 확보한 데이터가 음성뿐 |
| 목표 | 실시간 Dynamic KG + Trans-ST 임베딩 | **교신 1건 단위 Event-centric KG + 규칙 기반 질의 데모** | KG 평가는 생략하고 데모로 대체, 범위 축소 |
| 핵심 논리 | "KG를 만든다" | **"왜 하필 KG여야 하는가"를 샘플에서 관찰한 문제로 증명** | 평가 기준이 "문제/도메인에 맞는 기술 + reference"이기 때문 |
| 한국 관련 내용 | 본론 | **RQ4 검증 대상**(한국·일본 교신에 그대로 적용해 일반화 확인). 한국 기준 구축은 향후 과제 | 팀 합의: 미국 기준으로만 구축 |

---

## 1. 샘플 음성 분석: 실제로 관찰된 문제 (직접 측정)

샘플에 Whisper large-v3를 두 가지 설정(VAD 사용/미사용)으로 돌리고, 신호를 측정했다. 원문은 `stt_sample/*.txt`에 있다.
※ 오인식으로 보이는 부분의 "실제 발화"는 녹음을 직접 들어서 확인해야 하며, 아래 표기는 추정이다.

### 1-1. 음향(신호) 수준

| # | 관찰 | 측정값 / 증거 | 의미 |
|---|---|---|---|
| A1 | **절반 이상이 완전 무음** | 248초 중 **57.5%가 디지털 무음**(-90 dB 미만). 스켈치(squelch) 또는 편집으로 송신 사이가 잘려 있음 | 무음 구간에서 Whisper가 **없는 말을 지어냄(hallucination)**: VAD를 끄면 `"Thanks for watching."`, `"you"`, `"Thank you."`가 출력됨 → 그대로 두면 **KG에 가짜 노드가 생긴다** |
| A2 | **협대역 음성** | 음성 에너지의 90%가 **240–1720 Hz**에 몰려 있음 (VHF AM 무전 특성, 전화보다 좁음) | 고주파 자음(s, f, th)이 사라져서 숫자, 알파벳(phonetic alphabet), 콜사인이 헷갈림 |
| A3 | 송신 안의 SNR은 **나쁘지 않음** | 송신 구간별로 추정 SNR **20–39 dB**, 클리핑 없음 | **"음질" 자체보다는 A1·A2와 아래 언어적 문제가 더 크다** (발표 때 이 점을 꼭 짚을 것) |
| A4 | **화자 전환 간격이 매우 짧음** | 송신 사이 간격이 0.1–0.5초인 곳이 여러 군데 (177.5→177.7s, 242.0→242.1s, 246.3→246.5s) | 무음 길이만으로는 화자를 나눌 수 없음. 실제로 Whisper가 **관제사와 조종사 발화를 한 세그먼트로 합침**: `"heavy departed. Copy that, heavy. Clear to land, 28 right, American 20-69."` |

### 1-2. 언어·의미 수준 (KG가 필요한 이유는 여기서 나옴)

| # | 관찰 (샘플 전사) | 문제 유형 |
|---|---|---|
| L1 | 같은 음성인데 한 번은 `Delta 2398`, 다른 설정에서는 **`Alpha 2398`**. 바로 이어지는 조종사 복창에는 `Delta 2398`이 나옴 | **콜사인 오인식**. 앞뒤 교신(문맥)을 보면 고칠 수 있음 |
| L2 | `American 20-69` / `2069`, `two two right` / `22 right`, `19-987` | **숫자·형식 정규화**가 필요함 (같은 개체가 여러 문자열로 나옴) |
| L3 | `Terra`(→Tower?), `north-south`(→**NorCal** Departure?), `skip away`(→**give way**), `you have a 1947`(→United 1947?) | **도메인 고유명사·관용 표현 오인식**. 시설·항공사 사전(KB)이 있으면 교정할 수 있음 |
| L4 | 관제사: `Delta 2398, turn right at Juliet, cross runway 22 right, continue via Alpha to your ramp` → 조종사: `All right, right turn Juliet, cross 22 right at Juliet, Alpha to the ramp, Delta 2398` | **복창은 바꿔 말하기(paraphrase)**: 순서가 바뀌고, 콜사인이 뒤로 가고, 단어가 달라짐 → **단어 단위 비교는 실패하고 개념 단위로 비교해야 함** (Helmke et al., 2021과 같은 결론) |
| L5 | 마지막 복창 `Alpha, alpha one, two.` | **콜사인이 빠진 복창일 가능성**. FAA JO 7110.65 §2-4-3은 관제사가 복창에 콜사인이 포함되도록 확인하라고 규정함 → **규칙으로 탐지할 수 있는 이상 상황** |
| L6 | 한 클립 안에 runway 22R/Juliet/Alpha(SFO가 아닌 공항으로 보임)와 SFO 28L/28R, Tower/Ground 주파수가 섞여 있음 | **맥락(공항·주파수)이 바뀜**. 공항별 정적 지식이 없으면 "22R이 이 공항에 존재하는가?"를 검증할 수 없음 |
| L7 | `heavy departed`, `parallel runway`, `visual` | **명시되지 않은 상황 정보**(후류 경고, 교통 정보)가 섞여 있음 → 지시(instruction)와 정보(advisory)를 구분해야 함 |
| L8 | 지시(관제사) → 복창(조종사) → 이관(`contact ground`)이 **여러 발화에 걸쳐** 이어짐 | 한 문장 안이 아니라 **발화들 사이의 관계**가 핵심임 → 그래프 구조가 필요함 |

**정리:** 사용자가 직관적으로 느낀 "음질"과 "화자 전환" 문제는 실제로 있다(A1, A2, A4). 그런데 측정해 보면 SNR보다는 **(1) 무음 구간 환각, (2) 협대역 때문에 생기는 콜사인·숫자 오인식, (3) 화자 경계를 알 수 없음, (4) 발화 사이 관계(지시↔복창↔이관)를 문자열로는 표현할 수 없음**이 진짜 문제다. (4)가 KG를 쓰는 논리적 근거가 된다.

---

## 2. 문제정의 (다듬은 버전)

### 2-1. 한 문장 문제정의
> 미국 ATC VHF 교신은 **잡음 섞인 다화자 음성으로만** 존재한다. 그래서 "누가(어느 항공편이) 무엇을 지시받았고, 제대로 복창했는가"라는 **안전에 직결되는 정보를 기계가 질의·검증할 수 없다.** STT 텍스트만으로는 부족하다. 오인식, 표기 변이, 바꿔 말하기, 발화 간 관계 때문에 **단어 수준 표현이 개념 수준의 일치 여부를 보여주지 못하기 때문이다.**

### 2-2. 연구 질문 (RQ)
- **RQ1.** ATC 온톨로지(SESAR/DLR, MITRE)에 기반한 Event-centric KG가, STT 오류가 있는 미국 교신에서 **지시–복창 쌍과 항공편별 상황 이력**을 개념 단위로 표현할 수 있는가?
- **RQ2.** 정적 도메인 지식(공항 레이아웃, 시설, 항공사 콜사인)과 발화 간 링크를 이용하면 **STT 오류(L1, L3)를 탐지하고 교정**할 수 있는가?
- **RQ3.** 이렇게 만든 KG 위에서 **복창 불일치·콜사인 누락·활주로 중복 허가** 같은 상황을 질의로 찾아낼 수 있는가? (→ 데모)
- **RQ4.** 미국 기준으로 구축한 파이프라인을 한국·일본 관제 교신에 **추가 학습 없이** 적용하면 어느 모듈에서 인식이 떨어지고, 지역 자원(M0)만 바꿔도 회복되는가? (자세한 실험 설계는 [문제정의.md](문제정의.md) §7)

### 2-3. 범위
- **구축 대상:** HF LiveATC 아카이브(`nyuuzyou/liveatc`)의 **KATL(애틀랜타)** Ground·Delivery·Departure 교신, 영어, 오프라인 일괄 처리(batch), 데모 질의. 이 데이터셋에는 KSFO가 없고 KATL은 Tower 피드가 없다 (자세한 내용은 system_module.pdf §2)
- **데이터 선정 근거:** 공개 ATC 코퍼스 11종을 요구사항 7개(미국 실제 운영, 관제사·조종사 양측, 연속 녹음, 맥락 메타데이터, 즉시 확보, RQ4 연계, 정답 전사)로 비교한 결과, 앞의 다섯 가지를 동시에 만족하는 것은 LiveATC 계열뿐이었다. 비교표와 논리는 system_module.pdf §2.2에 있다. 보조 후보: TartanAviation KAGC(Tower + ADS-B), LDC ATC0(정답 전사, 유료)
- **검증 대상 (RQ4):** 한국(RKSI)·일본(RJTT) 교신 국가별 15–20개. 구축에는 쓰지 않고 그대로 적용해 검증만 한다
- **제외:** 한국·일본 데이터로 학습·미세조정, 자국어 발화 인식, 실시간 처리, KG 임베딩/완성(Trans-ST 등), 정량적 KG 평가 (교수 지시에 따라 데모로 대체)

### 2-4. 왜 DB 테이블이나 NER 출력이 아니라 **KG**인가 (발표에서 반드시 나올 질문)
1. **n-항 관계 + 발화 간 관계:** 지시 하나는 (항공편, 명령유형, 값, 단위, 조건/한정어)로 된 n-항 관계다. 이게 다시 복창·이관 발화와 연결된다. 평평한 트리플 하나로는 표현할 수 없으므로 **사건(Event) 노드로 reification**하는 것이 표준 패턴이다 (W3C n-ary relations note).
2. **정적 KB에 연결(grounding):** "22R", "Juliet", "NorCal"을 실제 공항 노드에 연결해야 오인식을 검증할 수 있다 (NASA ATM Ontology의 Airport/Runway/Facility 개념).
3. **엔티티 통합:** `Delta 2398`, `2398`, `Alpha 2398`(오인식)을 하나의 Flight 노드로 합치는 일은 그래프에서 자연스럽다.
4. **시간 순서 질의:** "지금 28L에 착륙 허가를 받은 항공편은?", "DAL2398이 받은 지시 이력은?" 같은 질문은 그래프 탐색(경로 질의)이 된다.

---

## 3. 문제 → 요구사항 → 기술 → Reference (논리 매핑표, PPT 핵심 슬라이드)

| 관찰된 문제 | 요구사항 | 적용 기술 | 근거 Reference |
|---|---|---|---|
| A1 무음 구간 환각 | 송신 단위 분할, 환각 필터 | VAD (디지털 무음/스켈치 감지) + Whisper `no_speech_prob`·`avg_logprob` 필터 | [R7] Whisper, [R9] Whisper-ATC 계열 |
| A2, L1, L3 협대역·콜사인·고유명사 오인식 | 도메인 적응 STT, 문맥 기반 바이어싱 | Whisper large-v3 + **도메인 어휘 prompt**(활성 콜사인, 유도로, 시설명). 가능하면 ATC로 미세조정한 모델과 비교 | [R8] 감시데이터로 콜사인 boosting (+53.7%p abs.), [R9] Whisper-ATC, [R10] 미국 억양 성능 저하 |
| A4 화자 경계와 역할 | 관제사/조종사 역할, 화자 전환 검출 | 텍스트 기반 역할 분류 (콜사인 위치 단서: 관제사는 문장 앞, 조종사는 문장 뒤) | [R11] BERTraffic |
| L4, L8 바꿔 말하는 복창, 발화 간 관계 | **개념 단위** 표현 | **ATC 온톨로지**(callsign, command type, value, unit, qualifier)로 의미를 추출 | [R1] SESAR PJ.16-04 온톨로지, [R2] Kleinert 2021, [R3] Helmke 2021 |
| 미국 데이터 특수성 (niner, altimeter, 표기 차이) | 미국식 어휘·개념 반영 | DLR 온톨로지에 MITRE와의 비교 결과를 반영해 미국판으로 조정 | [R4] Chen et al. 2023 (MITRE–DLR) |
| 데이터 부족 (라벨 없음) | 적은 예시로 추출 | **LLM few-shot / 스키마 기반 구조화 추출** (JSON) | [R12] Mekerishvili et al. 2025, [R13] LLM-KGC survey, [R14] ATCO2 NER |
| L2, L6 정규화·맥락 검증 | 정적 KB 연결, 엔티티 통합 | NASA atmonto 클래스 재사용, 공항 레이아웃 KB, 콜사인 사전 | [R5] NASA ATM Ontology, [R15] FAA 콜사인 규정 |
| n-항 지시와 시간 흐름 | 사건 중심 시간 KG | 송신(Transmission)과 지시(Instruction)를 **Event 노드로 reify**, 타임스탬프와 출처(provenance) 저장 | [R16] W3C n-ary, [R17] Event-centric TKG survey, [R18] Hogan et al. KG |
| L5 등 이상 상황 탐지 | 도메인 규칙 기반 추론 | Cypher 규칙: 복창 일치 여부, 콜사인 누락, 활주로 중복 허가 | [R6] FAA JO 7110.65 §2-4-3, [R3] Helmke 2021, [R19] SCOPE |

---

## 4. KG 스키마 (Neo4j property graph 기준)

```
[정적 계층: Domain KB]  (NASA atmonto 클래스명 차용)
(:Airport {icao:'KSFO'})-[:HAS_RUNWAY]->(:Runway {id:'28L'})
(:Airport)-[:HAS_TAXIWAY]->(:Taxiway {id:'A1'})
(:Facility {name:'NorCal Departure', type:'TRACON'})
(:Airline {icao:'DAL', telephony:'DELTA'})

[동적 계층: Event-centric]  (SESAR/DLR 온톨로지의 command 구조를 n-ary로 reify)
(:Transmission {t_start, t_end, raw_text, norm_text, asr_conf, audio_ref})
   -[:SPOKEN_BY]->(:Role {name:'ATCO'|'PILOT'})
   -[:ADDRESSES]->(:Flight {callsign:'DAL2398'})-[:OPERATED_BY]->(:Airline)
(:Transmission)-[:CONTAINS]->(:Instruction {type:'CROSS_RUNWAY', value, unit, qualifier, conf})
(:Instruction)-[:TARGET]->(:Runway|:Taxiway|:Facility)
(:Transmission {role:PILOT})-[:READBACK_OF {status:'MATCH'|'PARTIAL'|'MISMATCH'|'NO_CALLSIGN'}]->(:Transmission {role:ATCO})
(:Instruction)-[:NEXT {dt}]->(:Instruction)        // 항공편별 시간 순서
(:Flight)-[:ALIAS {source:'asr'}]->(:CallsignMention {text:'Alpha 2398'})   // 오인식 흔적 보존
```

**설계 원칙**
- 모든 추출 노드에 출처(`Transmission`)와 신뢰도(`conf`)를 남긴다. STT가 불확실하므로 **"그래프가 무엇을 근거로 이렇게 판단했는지"**를 데모에서 보여줄 수 있어야 한다.
- 명령 유형(`type`)은 DLR 온톨로지 용어를 우선 쓰고, 미국에만 있는 표현은 별도 표시한다: `CLEARED_TO_LAND`, `TAXI_VIA`, `CROSS_RUNWAY`, `HOLD_SHORT`, `CONTACT`, `GIVE_WAY`, `TRAFFIC_INFO` 등.

---

## 5. 구축 파이프라인 (개발 담당용)

| 단계 | 내용 | 도구 | 산출물 |
|---|---|---|---|
| 0. 데이터 | HF LiveATC(`nyuuzyou/liveatc`) KATL 피드. `Gnd-All` 11개 파일(00:00–05:30Z)과 같은 시간대 `Del`·`Dep`. CTAF·Company는 제외하고 ATIS는 맥락으로만 사용. LiveATC 약관상 비상업 용도이고 재배포 금지 | huggingface_hub, zstd, tar | `audio/{feed}/*.mp3` + 파일명 메타데이터 |
| 1. 분할 | 디지털 무음/스켈치 기준으로 송신 단위 분할 → Silero VAD로 보정 | ffmpeg, silero-vad | 송신별 클립과 타임스탬프 |
| 2. STT | Whisper large-v3 + 도메인 prompt. 환각 필터 적용. (선택) ATC 미세조정 모델과 비교 | faster-whisper (RTX 4090에서 동작 확인) | `transcripts.jsonl` |
| 3. 역할/화자 | 콜사인 위치 규칙 + LLM 분류로 ATCO/PILOT 판별 | LLM | role 라벨 |
| 4. 의미 추출 | 온톨로지 스키마를 넣은 few-shot 프롬프트 → `{callsign, instructions:[{type,value,unit,qualifier,target}]}` JSON | Claude API 등 LLM | `semantics.jsonl` |
| 5. 정규화·grounding | 콜사인을 ICAO 코드로(DELTA→DAL), 숫자 정규화, 활주로/유도로를 정적 KB와 매칭. 매칭 실패는 플래그 | Python, 공항 레이아웃 KB(FAA NASR / OurAirports) | 정규화된 엔티티 |
| 6. KG 적재 | 정적 KB를 먼저 적재한 뒤 이벤트 적재 | Neo4j (Desktop 또는 Docker) + Cypher | 그래프 DB |
| 7. 추론·데모 | 아래 질의 4종 + Neo4j Bloom/Browser 시각화 | Cypher | 데모 시나리오 |

**데모 질의 예시 (발표 하이라이트)**
1. **복창 검증:** 각 ATCO 지시마다 30초 안의 같은 Flight PILOT 송신을 찾아 Instruction 집합을 비교하고 MATCH/PARTIAL/MISMATCH를 매김 (샘플 L4: DAL2398 → MATCH)
2. **콜사인 누락 복창:** `READBACK_OF`인데 `ADDRESSES`가 없는 PILOT 송신 (샘플 L5)
3. **STT 교정:** 지시와 복창의 콜사인이 다를 때 그래프 문맥(복창, 이전 이력, 항공사 사전)으로 해결 (샘플 L1: Alpha 2398 → DAL2398)
4. **상황 인식:** "지금 시각 기준 28L에 착륙 허가가 나간 항공편과 직전 출발 heavy" 같은 시간 창 질의

---

## 6. Reference (검증 완료 / 기존 기획서 인용 정정 포함)

### 6-1. 핵심 Reference (실제 존재와 서지를 확인함)
- **[R1]** Helmke, H. et al. (2018). *Ontology for Transcription of ATC Speech Commands of SESAR 2020 Solution PJ.16-04.* 37th DASC. doi:10.1109/DASC.2018.8569238 — https://elib.dlr.de/123237
- **[R2]** Kleinert, M., Helmke, H., Shetty, S., Ohneiser, O., Ehr, H., Prasad, A., Motlicek, P., Harfmann, J. (2021). *Automated Interpretation of Air Traffic Control Communication: The Journey from Spoken Words to a Deeper Understanding of the Meaning.* 40th DASC. doi:10.1109/DASC52595.2021.9594387
- **[R3]** Helmke, H. et al. (2021). *Readback Error Detection by Automatic Speech Recognition to Increase ATM Safety.* 14th USA/Europe ATM R&D Seminar. — https://publications.idiap.ch/publications/show/4728 (후속: SESAR Innovation Days 2022, Isavia 실데이터 기준 readback error 81% 탐지)
- **[R4]** Chen, S., Helmke, H., Tarakan, R. M., Ohneiser, O., Kopald, H., Kleinert, M. (2023). *Effects of Language Ontology on Transatlantic Automatic Speech Understanding Research Collaboration in the Air Traffic Management Domain.* Aerospace 10(6), 526. doi:10.3390/aerospace10060526
- **[R5]** Keller, R. M. (2017). *The NASA Air Traffic Management Ontology.* NASA/TM-2017-219526. — https://ntrs.nasa.gov/citations/20170006095 (온톨로지: data.nasa.gov/ontologies/atmonto)
- **[R6]** FAA Order JO 7110.65 *Air Traffic Control*, §2-4-3 Pilot Acknowledgment/Readback. (발표 전에 faa.gov 최신판에서 조항 번호를 다시 확인할 것)
- **[R7]** Radford, A. et al. (2023). *Robust Speech Recognition via Large-Scale Weak Supervision* (Whisper). ICML.
- **[R8]** Nigmatulina, I., Zuluaga-Gomez, J., Prasad, A., Sarfjoo, S. S., Motlicek, P. (2022). *A Two-Step Approach to Leverage Contextual Data: Speech Recognition in Air-Traffic Communications.* ICASSP. / Zuluaga-Gomez et al. (2021). *Contextual Semi-Supervised Learning…* Interspeech (LiveATC 테스트셋, CA-WER 지표).
- **[R9]** van Doorn, J. et al. Whisper-ATC (ATCO2·ATCOSIM으로 Whisper 미세조정, WER 13.5% / 1.17%). TU Delft. ⚠ 정확한 제목과 학회는 TU Delft repository에서 확인 필요
- **[R10]** *Fine-Tuning Whisper for American English Air Traffic Control Speech Recognition: A Data-Efficient Pipeline.* Research Square preprint. (유럽 데이터로 학습한 모델이 미국 교신에서 WER 30.3%로 떨어진다고 보고) ⚠ 프리프린트이며 저자 확인 필요. **"왜 미국 데이터에 따로 맞춰야 하나"의 근거**로 활용
- **[R11]** Zuluaga-Gomez, J. et al. (2023). *BERTraffic: BERT-based Joint Speaker Role and Speaker Change Detection for ATC Communications.* IEEE SLT. arXiv:2110.05781
- **[R12]** Mekerishvili, A. M., Sun, J., Jonk, P., de Vries, V. (2025). *Structured Command Extraction from ATC Communications Using Open and Fine-Tuned Language Models.* SESAR Innovation Days 2025 (TU Delft MSc thesis 기반. Llama 3.3 70B 기준 micro-F1 0.95 (사람 전사) / 0.86 (Whisper 전사))
- **[R13]** *LLM-empowered Knowledge Graph Construction: A Survey.* arXiv:2510.20345 (2025) — 온톨로지 → 추출 → 융합 3단계, schema-based 패러다임
- **[R14]** Zuluaga-Gomez, J. et al. (2024). *ATCO2 corpus: A Large-Scale Dataset for Research on ASR and NLU of ATC Communications.* DMLR. arXiv:2211.04054
- **[R15]** FAA JO 7340.2 *Contractions* (항공사 telephony↔ICAO 3자리 코드). 데이터 소스로 사용
- **[R16]** Noy, N., Rector, A. (2006). *Defining N-ary Relations on the Semantic Web.* W3C WG Note. https://www.w3.org/TR/swbp-n-aryRelations/
- **[R17]** Knez, T., Žitnik, S. (2023). *Event-Centric Temporal Knowledge Graph Construction: A Survey.* Mathematics 11(23), 4852. doi:10.3390/math11234852
- **[R18]** Hogan, A. et al. (2021). *Knowledge Graphs.* ACM Computing Surveys 54(4).
- **[R19]** Deng, Q. et al. *SCOPE: A Lightweight-training LLM Framework for ATC Readback Monitoring.* arXiv:2605.29543 (2026) — 최신 동향 슬라이드용
- (데이터) Godfrey, J. J. (1994). *Air Traffic Control Complete* (ATC0). LDC94S14A. doi:10.35111/2bg6-nn53

### 6-2. 기존 기획서(ver_A) 인용 정정, **반드시 고칠 것**
| ver_A 표기 | 문제 | 정정 |
|---|---|---|
| NASA's ATM Ontology… (NASA Ames, **2015**) | 연도·제목 부정확 | Keller (2017) NASA/TM-2017-219526 [R5] |
| Readback Error Detection… **to Support ATCos** (2021), "개념 수준 **지식 그래프** 상에서 추론" | 제목이 다르고 내용 과장. 이 논문은 KG가 아니라 **온톨로지 개념 비교** | "…to Increase ATM Safety" [R3]. "온톨로지 개념 단위 비교"로 수정. **KG로 확장한 것이 우리 기여**라고 쓰면 오히려 논리가 강해짐 |
| NLU for ATC: US and European Ontologies (DLR & MITRE, **2024~2025**) | 그런 제목의 논문은 찾을 수 없음 | Chen et al. (2023) Aerospace [R4] |
| Intelligent ATM System Based on KG (**Trans-ST**) (CAUC, 2024) | **검색으로 확인되지 않음 (환각 의심)** | 삭제. 시공간 KG가 필요하면 Jia et al. (2022) *Sequence Encoder-based Spatiotemporal KG Completion*, J. Web Eng.(STSE)로 대체. 이번 범위에서는 빼는 것을 권장 |
| ATCO2: "5,000시간 이상 **음성-ADS-B 융합 벤치마크**" | 과장 | 수작업 전사는 **4시간 테스트셋**뿐이고, 5,281시간은 **라벨 없는 자동 전사(pseudo-label)**. 유료(ELRA-S0484). 대부분 유럽 공항 |
| ATCOSIM 권장 | 시뮬레이터 + 유럽 억양 | 미국 기준 학습에는 맞지 않음. 비교용으로만 언급 |
| FAA Speech Database (LDC) | 정확한 이름 아님 | LDC ATC0 (Godfrey 1994), 유료, 1990년대 데이터 |

### 6-3. 데이터 링크 점검
- 플레이리스트 *"ATC communication practice"*: 사용할 수 있음 (출처 공항/주파수 메타데이터를 따로 기록할 것. L6 문제)
- `youtu.be/o8Uxz1-mcGA` = **"Aircraft Carrier Communications – Air Force Radio Chatter Sounds Effect | 10 Hours ASMR"**: **효과음/ASMR 영상이라 실제 관제 교신으로 볼 수 없음 → 학습·KG 데이터에서 제외 권장**

---

## 7. 역할분담 제안 (개발 / PPT / 발표)

| 역할 | 담당 범위 | 첫 주 할 일 |
|---|---|---|
| **개발** | §5 파이프라인, Neo4j 적재, 데모 질의 4종 | 샘플로 1→6단계를 끝까지 한 번 돌리기 (STT는 `stt_sample/stt.py` 재사용) |
| **PPT** | §1 문제 증거(전사 대조), §3 매핑표, §4 스키마 그림, §6-2 정정 | 매핑표(§3)를 슬라이드 뼈대로 잡고, 각 행에 샘플 근거를 1개씩 붙이기 |
| **발표** | 스토리라인 + 예상 질문 방어 | §2-4 "왜 KG인가" Q&A 준비. "KG 평가는 왜 없나"에는 범위 결정과 데모로 답하기 |

**발표 스토리 (논리 흐름):** 실제 교신 음성 → *"STT만으로는 안 되는 이유"* (샘플 전사의 실패 사례 L1/L4/L5) → *"그래서 개념 단위 + 발화 간 관계 + 정적 지식이 필요하다"* → 각 요구사항에 대응하는 검증된 선행연구 → 우리 KG 스키마 → 데모(복창 검증, 오인식 교정) → 한계와 향후 과제(한국 확장)
