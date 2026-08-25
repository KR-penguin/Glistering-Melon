# 실행 방법

프론트엔드는 이 PC에서, 백엔드는 집 서버(`192.168.45.100`)에서 돕니다.
백엔드는 `restart: unless-stopped` 라서 **서버가 켜져 있으면 자동으로 떠 있습니다** —
평소에는 프론트만 켜면 됩니다.

---

## 1. 프론트엔드 (이 PC)

`start-frontend.bat` 더블클릭. 또는 터미널에서:

```bash
python -m http.server 5599
```

리포지토리 폴더에서 실행해야 합니다. 그다음 브라우저로:

**http://localhost:5599/Landing.dc.html**

멈추려면 그 창에서 `Ctrl+C`.

> 백엔드가 LAN 주소(`192.168.45.100`)라서 **같은 네트워크에 있어야** 동작합니다.
> 외부에서 접속하려면 포트포워딩 + 도메인이 필요합니다.

---

## 2. 백엔드 (집 서버)

### 상태 확인

```bash
ssh sean@192.168.45.100 "docker ps --filter name=gm- && curl -s localhost:8000/healthz"
```

`gm-backend`가 `Up`이고 `{"ok":true}`가 나오면 정상입니다.

### 꺼져 있을 때 시작

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose up -d"
```

### 재시작 (설정 바꾼 뒤)

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose up -d --build"
```

### 중지

```bash
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon/backend && docker compose down"
```

손님 마인크래프트 서버는 별도 컨테이너라 `down` 해도 계속 돕니다.

### 로그 보기

```bash
ssh sean@192.168.45.100 "docker logs -f --tail 50 gm-backend"
```

---

## 주소 모음

| | 주소 |
|---|---|
| 랜딩 페이지 | http://localhost:5599/Landing.dc.html |
| 로그인 | http://192.168.45.100:8000/login |
| 관리자 | http://192.168.45.100:8000/admin |
| 서버 상태 | http://192.168.45.100:8000/healthz |

관리자 토큰은 서버의 `.env`에 있습니다:

```bash
ssh sean@192.168.45.100 "grep ^ADMIN_TOKEN= /home/sean/glistering-melon/backend/.env"
```

---

## 코드 바꾼 뒤 배포

```bash
git archive --format=tar.gz -o /tmp/b.tar.gz HEAD backend
scp /tmp/b.tar.gz sean@192.168.45.100:/home/sean/glistering-melon/
ssh sean@192.168.45.100 "cd /home/sean/glistering-melon && tar xzf b.tar.gz && rm b.tar.gz && cd backend && docker compose up -d --build"
```

리포지토리가 private이라 서버가 직접 `git pull`을 못 합니다. GitHub에 deploy key를
등록하면 서버에서 바로 받아올 수 있습니다.

---

## DB 보기

SQLite 파일 하나입니다: 서버의 `/srv/minecraft/data/glistering_melon.db`

**전체 요약** — 테이블별 행 수와 주요 컬럼:

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview"
```

**특정 테이블만:**

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview servers"
```

테이블: `servers`, `users`, `applications`, `auth_tokens`

**모든 컬럼 보기** (기본은 주요 컬럼만):

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview servers --all-columns"
```

**직접 조회:**

```bash
ssh sean@192.168.45.100 "docker exec gm-backend python -m app.dbview --sql \"SELECT nickname, status, payment_status FROM servers\""
```

읽기 전용이라 `SELECT`만 됩니다. 실수로 고객 데이터를 바꿀 수 없게 SQLite를
읽기 전용 모드로 열고, `SELECT`가 아닌 문은 거부합니다. 값을 바꾸실 일은
관리자 페이지에서 하세요.

비밀번호 해시·RCON 비밀번호·세션 토큰은 출력에서 가려집니다.

**파일 백업** (통째로 복사):

```bash
scp sean@192.168.45.100:/srv/minecraft/data/glistering_melon.db ./backup.db
```

---

## 자주 쓰는 것

**손님 서버 목록**

```bash
ssh sean@192.168.45.100 "docker ps --filter name=gm- --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

**테스트 데이터 정리** (서버·계정 전부 삭제, 되돌릴 수 없음)

```bash
ssh sean@192.168.45.100 'for C in $(docker ps -aq --filter name=gm- --format "{{.Names}}" | grep -v backend); do docker rm -f $C; sudo rm -rf /srv/minecraft/data/worlds/$C; done; docker exec -i gm-backend python -c "
from app.db import get_connection
c=get_connection()
[c.execute(f\"DELETE FROM {t}\") for t in (\"auth_tokens\",\"users\",\"servers\",\"applications\")]
c.commit(); c.close()"'
```

**명령어 레퍼런스 갱신** (마인크래프트 새 버전 나왔을 때)

```bash
cd backend && python -m agent.rag.ingest --version 26.3
```

diff를 확인하고 커밋하세요.
