> Սա [README.md](README.md)-ի հայերեն թարգմանությունն է։ Տարբերության դեպքում հիմնական աղբյուրը անգլերեն [README.md](README.md)-ն է։

# GLDtk — Generative Level Design Toolkit

GLDtk-ն տեքստային նկարագրությունը վերածում է **խաղալու պատրաստ 2D platformer մակարդակի** [LDtk](https://ldtk.io)-ում՝ ուղիղ editor-ի ներսում։ Նկարագրում ես ամբողջ մակարդակը (բաժիններ, թշնամիներ, մետաղադրամներ, զենքեր, վտանգներ, տրամադրություն), և GLDtk-ն կառուցում է այն, ապացուցում է, որ այն հնարավոր է անցնել, և ասում է, թե prompt-ի որ մասը իրագործվել է։ Հետո կարող ես ընտրել կամ նկարագրել մակարդակի ցանկացած հատված և վերագեներացնել միայն այն։

Սա **neuro-symbolic pipeline** է. LLM-ը զբաղվում է ստեղծագործականությամբ, իսկ դետերմինիստական layout-ը, ֆիզիկան և ստուգումները՝ ճշտությամբ։ Սկավառակի վրա գրվող ամեն մակարդակ ստատիկ կերպով ստուգված է. ֆիզիկայի օրակլը ապացուցում է, որ խաղացողը կարող է հասնել Start-ից Exit՝ խաղի սեփական թռիչքի և կարողությունների (ability) կարգավորումներով։

---

## Բովանդակություն

1. [Արագ մեկնարկ](#quick-start)
2. [GLDtk sidebar-ի օգտագործումը](#using-the-gldtk-sidebar)
3. [LLM provider-ներ և տեղական fallback](#llm-providers-and-the-local-fallback)
4. [Ինչպես է աշխատում](#how-it-works)
5. [Մակարդակի կառուցվածք, առաքելություններ և pattern-ներ](#level-structure-missions-and-patterns)
6. [Repository-ի կառուցվածք](#repository-layout)
7. [Առանց ծրագրի. Python և HTTP](#without-the-app-python-and-http)
8. [Գնահատում](#evaluation)
9. [Թեստեր](#tests)
10. [Խնդիրների լուծում](#troubleshooting)
11. [Mac ծրագրի կառուցում](#build-the-mac-app)

---

<a id="quick-start"></a>

## Արագ մեկնարկ

Պահանջներ. macOS/Linux, Python 3.11+, Node.js (LDtk-ն գործարկելու համար) և LLM՝ անվճար [Gemini API key](https://aistudio.google.com/apikey) կամ տեղական [Ollama](https://ollama.com) մոդել։

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]"

echo 'GEMINI_API_KEY=your-key' > .env          # git-ignored; read by server.py
ollama pull qwen2.5:14b                        # optional: local fallback (about 9 GB)

./start.sh                                     # starts the backend, then LDtk
```

`start.sh`-ը ֆոնային ռեժիմում գործարկում է FastAPI backend-ը (`server.py`, `http://127.0.0.1:8765`), գործարկում է LDtk ծրագիրը `ldtk-src/app`-ից և LDtk-ի փակվելիս կանգնեցնում է backend-ը։ Առանձին գործարկելու համար.

```bash
source .venv/bin/activate && python server.py   # backend
cd ldtk-src/app && npm start                    # LDtk with the GLDtk sidebar
```

Նախընտրո՞ւմ ես պատրաստի ծրագիր։ `scripts/build_mac_app.sh`-ը կառուցում է չստորագրված `GLDtk.app` (տես [Mac ծրագրի կառուցում](#build-the-mac-app))։

`ldtk-src/`-ը LDtk-ի fork է ([github.com/bekiryan/ldtk](https://github.com/bekiryan/ldtk), `feat/v2` branch)՝ ներկառուցված GLDtk sidebar-ով։ Նրա Haxe կոդը փոխելուց հետո վերակառուցիր այն (տես [CLAUDE.md](CLAUDE.md#building-the-ldtk-integration-haxe))։

---

<a id="using-the-gldtk-sidebar"></a>

## GLDtk sidebar-ի օգտագործումը

Բացիր կամ ստեղծիր `.ldtk` project LDtk-ում. աջ կողմի sidebar-ը աշխատում է հենց այդ ֆայլի հետ։

- **Generate.** գրիր prompt, օրինակ՝ *«Արևոտ անտառ՝ 5 մետաղադրամով և մեկ slime-ով, հետո մութ քարանձավ՝ 2 չղջիկով, փշոտ փոսով և գանձերի սնդուկով, և վերջում ամրոց, որտեղ skeleton boss-ը պահպանում է ելքը։ Խաղացողը սկսում է սրով։»*, ապա սեղմիր **Generate** (կամ Ctrl+Enter)։ Մակարդակը հայտնվում է նույն տեղում։
- **Fidelity report.** prompt-ից հանված ամեն պահանջ ցուցադրվում է որպես ✅ բավարարված, ⚠ մոտարկված, ❌ անհնար կամ բացակայող, կամ · չստուգված (ոճային պահանջների համար, օրինակ «սարսափազդու»)՝ պատճառի հետ միասին։ *auto* նշված կետերը ավելացվել են դետերմինիստական completeness քայլով։
- **Edit** (վերագեներացնում է մակարդակի մի մասը. դրանից դուրս ամեն ինչ մնում է ճիշտ նույնը, ներառյալ քո ձեռքով արած փոփոխությունները).
  - **Region.** *Select area*՝ քաշիր ուղղանկյուն canvas-ի վրա և նկարագրիր, թե ինչ պետք է լինի այնտեղ։
  - **Section.** ընտրիր բաժին (օրինակ «Dark Cave») և նկարագրիր փոփոխությունը։
  - **Entities.** ավելացրու, հեռացրու կամ փոխիր միայն entity-ները («ուժեղացրու չղջիկներին», «այստեղ ավելացրու 3 մետաղադրամ»)՝ ընտրված entity-ների, ընտրված տարածքի կամ ամբողջ մակարդակի համար։
  - **Describe.** ասա, թե որտեղ («բոսից առաջ գտնվող անջրպետը»). GLDtk-ն լուսավորում է գտած տարածքը, իսկ դու հաստատում ես *Apply here*-ով։
- **↶ Undo last GLDtk edit.** ամեն generate և edit նախ snapshot է անում ֆայլը (կողքի `.gldtk_history/`-ում, պահվում է վերջին 30-ը)։ Undo-ն հետ է գնում դրանցով։
- **Game Profile.** ամբողջ խաղի համար՝ ֆիզիկա (ձգողականություն, թռիչքի արագություն, վազքի արագություն, քայլի բարձրություն), կարողություններ (double jump, dash, wall jump) և **design comfort limits** (ամենաերկար hop, ամենաբարձր վերելք, ամենաերկար անկում)։ Պահվում է project-ի կողքին՝ `gldtk.game.json`-ում։ Մակարդակները ստուգվում են դրա դեմ, և եթե prompt-ը պահանջում է կարողություն, որը profile-ում չկա, այն նշվում է ❌։ Լռելյայն ֆիզիկան՝ ձգողականություն 1800 px/s², թռիչքի արագություն 500 px/s, վազքի արագություն 220 px/s (կարճ hop՝ մոտ 4 tile բարձրություն, 7–8 tile հեռավորություն)։
- **Provider.** ընտրիր LLM-ը և ցանկության դեպքում՝ մոդելը։
- **Complexity.** slider (1–10) ամբողջ մակարդակի համար, իսկ ցանկության դեպքում՝ նաև յուրաքանչյուր բաժնի համար։ Այն կառավարում է թշնամիների և վտանգների խտությունը, թռիչքների խստությունը, ճյուղավորումը և առաքելության loop-երը։ Գեներացիայից հետո GLDtk-ն չափում է արդյունքը և ճշգրտում (ավելացնում կամ հեռացնում է թշնամիներ, նեղացնում կամ լայնացնում է անջրպետները), մինչև գնահատականը մոտենա քո թիրախին։
- **Rock fill.** խտության slider, որը չօգտագործված տարածքը լցնում է բնական քարանձավային ապարով (noise գումարած smoothing)։ Երթուղին, նրա թռիչքային աղեղները և headroom-ը երբեք չեն շոշափվում։
- **Progress և Cancel.** generate կամ edit-ի ժամանակ ցուցադրվում են կենդանի քայլերը (LLM փորձ, կրկնություն, մոդելի fallback, ստուգում, վերանորոգում)։ **Cancel**-ը կանգնեցնում է այն և ոչինչ չի գրում։
- **Route overlay.** canvas-ի վրա ցուցադրում է ստուգված Start→Exit երթուղին (քայլեր, hop-եր, անկումներ, օգտագործված կարողությունը), ինչպես նաև soft-lock հատվածները կարմիրով։ Թարմանում է քո ձեռքով արած փոփոխություններից հետո։
- **Difficulty chart.** յուրաքանչյուր բաժնի դժվարության կորը՝ զգուշացումներով (սուր թռիչք, հարթ հատված). կետի վրա պահիր կուրսորը՝ canvas-ի վրա նրա բաժինը թարթացնելու համար։
- **▶ Play.** LDtk-ում play ռեժիմ չկա, ուստի GLDtk-ն ավելացնում է այն։ Բացում է canvas modal, որն աշխատեցնում է մակարդակը Game Profile-ի ֆիզիկայով և կարողություններով (verifier-ից մոտ 1 px ճշտությամբ). pickup-ներ, կյանքեր, checkpoint-ներ, բանալիներ, վտանգներ, Exit և ստուգված երթուղու ghost։
- **Levels (worlds).** եթե prompt-ը նկարագրում է մի քանի մակարդակ (կամ սահմանված է *Levels* կարգավորումը, 1–8), ստեղծվում է world. մեկ outline call, ապա մեկ մակարդակ յուրաքանչյուր կետի համար, ամեն Exit կապված է հաջորդ Start-ին։ Fidelity գնահատականներով մակարդակների ցանկը թույլ է տալիս անցնել մակարդակների միջև. edit-ները և chart-ը գործում են բաց մակարդակի վրա, և կարող ես **վերագեներացնել միայն մեկ մակարդակ**՝ մնացածը թողնելով։
- **Structure.** յուրաքանչյուր բաժին ունի տեսակ (տես [Մակարդակի կառուցվածք](#level-structure-missions-and-patterns))։ Բաժնի կառուցվածքը փոխելու համար ընտրիր **Make this section a…**. LLM call պետք չէ։
- **Memory.** panel, որը ցույց է տալիս՝ GLDtk-ն ինչ է սովորել քո փոփոխություններից. խմբագրում, ջնջում, reset և անջատման փոխանջատիչ (տես [Design memory](#design-memory))։

Մակարդակները օգտագործում են ընտրված CC0 asset library (8 tile theme և 25 entity sprite, տես `assets/LICENSES.md`)։ Մի քանի բաժին ունեցող մակարդակը ստանում է մեկ tile layer յուրաքանչյուր section theme-ի համար։ GLDtk-ի վիճակը (prompt, բաժիններ, պահանջներ, spec, profile-ի ուղի) պահվում է մակարդակի custom field-երում, այնպես որ `.ldtk` ֆայլը միակ ճշմարտության աղբյուրն է։

---

<a id="llm-providers-and-the-local-fallback"></a>

## LLM provider-ներ և տեղական fallback

| Provider | Key (`.env`) | Լռելյայն մոդել | Նշումներ |
|---|---|---|---|
| `gemini` (լռելյայն) | `GEMINI_API_KEY` | `gemini-3.8-flash` | Fallback է անում `gemini-3.1-flash-lite`-ին, ապա տեղական մոդելին |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` | Prompt caching system prompt-ի վրա |
| `ollama` | չկա | `llama3.1` | Ամբողջովին տեղական. լավագույն արդյունքի համար օգտագործիր `qwen2.5:14b` կամ ավելի մեծ |
| `openai` / `deepseek` | `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | `gpt-4o-mini` / `deepseek-chat` | Best effort |

Gemini-ն, Claude-ը և Ollama-ն օգտագործում են schema-enforced structured output (`LevelSpec.json_schema()`)։

**Ինչու է Gemini-ն երբեմն ձախողվում.** անվճար tier-ը թույլ է տալիս օրական մոտ 20 հարցում յուրաքանչյուր Flash մոդելի համար (HTTP 429), Google-ի սերվերները հաճախ ծանրաբեռնված են (503 «high demand»), իսկ հին մոդելները հանվում են շրջանառությունից (404)։ Սրանցից ոչ մեկը GLDtk-ի սխալ չէ։ Երբ Gemini-ն օգտագործում ես լռելյայն մոդելով, GLDtk-ն փորձում է `gemini-3.8-flash`, ապա `gemini-3.1-flash-lite`, ապա **տեղական մոդել**։ Տեղական մոդելը սահմանվում է `GLDTK_LOCAL_FALLBACK`-ով (լռելյայն `ollama:qwen2.5:14b`. անջատելու համար դիր `off`)։ Երբ պատասխանել է fallback-ը, sidebar-ը ցույց է տալիս զգուշացում։ Տեղական 14B մոդելը Apple M-series Mac-ի վրա մեկ մակարդակի համար պահանջում է մոտ 1–5 րոպե։

---

<a id="how-it-works"></a>

## Ինչպես է աշխատում

```
Prompt
  │
  ▼  LLMController (llm/validator.py)            LLM → LevelSpec JSON (spec/level_spec.py):
  │    generate → validate → symbolic patch →     size, sections (with structure type and style),
  │    re-prompt with precise errors              graph, entities, requirements, complexity.
  │                                               Retrieved patterns and your design memory
  │                                               are added to the prompt.
  ▼
  Completeness (pipeline/completeness.py)         fidelity check; deterministic fill of missing
  │                                               entities/items; at most one LLM re-prompt
  ▼
  Terrain + layout (layout/terrain/, spec_layout) per-section generators carve rock into floors,
  │  (+ mission placement, mission/)               walls, corridors, labyrinths…; keys, locks and
  │                                               gates are placed from the mission graph
  ▼
  Verify + repair (layout/verify.py, softlock.py) extract the graph from the final tile grid, prove
  │                                               Start→Exit with hitbox-swept arcs and the Game
  │                                               Profile's abilities, and prove there is no
  │                                               soft-lock; repair, or re-prompt as a last resort
  ▼
  Comfort pass (layout/comfort.py)                short hops and walk-first paths (Game Profile
  │                                               comfort limits)
  ▼
  Densify (layout/densify.py)                     extra reachable platforms and ground
  ▼
  Complexity loop (pipeline/complexity.py)        measure difficulty, adjust toward the target
  ▼
  Rock fill (layout/fill.py, noise.py)            optional noise fill outside the protected route
  ▼
  Aesthetic (aesthetic/)                          per-section themes, autotiling, entity sprites
  ▼
  LDtk export (layout/ldtk_adapter.py)            LDtk 1.5.3 JSON with typed entity fields,
                                                  validated against LDtk's JSON schema
```

**Physics oracle** (`symbolic/physics.py`). թռիչքները ստուգվում են կինեմատիկ հավասարմամբ՝ `½g·t² − v·t + Δy = 0`, որը ընդլայնված է double jump-ի (շղթայված աղեղներ), dash-ի, wall jump-ի և յուրաքանչյուր բաժնի `gravity_scale`-ի համար։ Այն դետերմինիստական է և տևում է միլիվայրկյաններ, ուստի աշխատում է ամեն թեկնածուի վրա։

**Symbolic patcher** (`llm/patcher.py`). նախքան ևս մեկ LLM call ծախսելը, դետերմինիստական patch-երը ուղղում են տարածված սխալները (դեպի ներքև Jump-ը վերադասակարգվում է որպես Fall, սխալ մեկնարկային արագություններ, բացակայող աստիճանաքարեր, բացակայող Start կամ Exit)։

**Regional editing** (`edit/`). կարդացվում է ընթացիկ ֆայլը (grid, entity-ներ, բաժիններ)։ Այն բջիջները, որտեղ խաղացողի ուղիները հատում են region-ի եզրագիծը, **locked seams** են։ LLM-ը վերագեներացնում է միայն region-ը՝ region-ի լոկալ կոորդինատներով։ Արդյունքը կարվում է առկա JSON-ի մեջ, 1 tile լայնությամբ լուսանցքը նորից autotile է արվում, և **ամբողջ մակարդակը նորից ստուգվում է**։ Ստուգումը չանցած մակարդակը երբեք չի գրվում։

Դիզայնը և մաս առ մաս պատմությունը գտնվում են [docs/v2/DESIGN.md](docs/v2/DESIGN.md) և [docs/v2/PROGRESS.md](docs/v2/PROGRESS.md) ֆայլերում։

---

<a id="level-structure-missions-and-patterns"></a>

## Մակարդակի կառուցվածք, առաքելություններ և pattern-ներ

### Բաժնի կառուցվածքի տեսակներ

LLM-ը ընտրում է տեսակ յուրաքանչյուր բաժնի համար, իսկ դետերմինիստական generator-ները (`layout/terrain/`) պինդ քարից փորագրում են տեղանքը, այնպես որ մակարդակներն ունենում են հատակներ, պատեր, առաստաղներ և սենյակներ, ոչ թե միայն հարթակներ։

| Տեսակ | Ինչ ես ստանում |
|---|---|
| `corridor` | պատերով անցուղի՝ հատակով և առաստաղով, հիմնականում քայլելու համար |
| `parkour` | հարթակներ և կարճ hop-եր անջրպետի կամ փոսի վրայով |
| `labyrinth` | սենյակների և ճյուղերի լաբիրինթոս՝ փակուղիներով |
| `arena` | մեծ սենյակ մարտի համար |
| `shaft` | ուղղահայաց վերելք եզրերով |
| `hub` | կենտրոնական սենյակ՝ մի քանի ելքով |
| `open` | բաց հող և մի քանի հարթակ |

Ուղիները հիմնականում **քայլել, կռվել և կարճ hop անել** են. Game Profile-ի comfort limits-ը սահմանափակում է hop-ը, վերելքը և անկումը, և երթուղին նախընտրում է քայլելը։ Բաժիններն ունեն նաև **style**. `walk` (ամեն թռիչք հարմար է), `mixed` (լռելյայն. երբեմն ավելի երկար թռիչք) կամ `parkour` (երկար թռիչքներ մինչև ֆիզիկայի սահմանը, միայն երբ prompt-ը պահանջում է)։ Ընտրիր բաժինը և օգտագործիր **Make this section a…**՝ այն որպես այլ տեսակ նորից փորագրելու համար առանց LLM call-ի։

### Soft-lock-ի հայտնաբերում

Երաշխիքը «խաղացողը երբեք չի կարող խրվել» է, ոչ միայն «Start→Exit գոյություն ունի»։ GLDtk-ն հաշվում է շարժման գրաֆի ուժեղ կապակցված բաղադրիչները (strongly connected components) և **item-state graph** (դիրք գումարած պահվող բանալիներ, կարողություններ և անջատիչներ)։ Հատվածը, որին խաղացողը կարող է հասնել, բայց չի կարող դրանից դուրս գալ դեպի Exit (փոս առանց ելքի, դուռ, որի բանալին դրա ետևում է, բանալուց անդին անկում), սխալ է։ Դետերմինիստական վերանորոգումները ավելացնում են եզր կամ միակողմանի ելք, կամ տեղափոխում են բանալին։ Soft-lock հատվածները route overlay-ում երևում են կարմիրով։

### Mission graph-եր

Բանալիներ, կողպեքներ, կարողություններ կամ հետադարձ գնալ պարունակող prompt-ների համար GLDtk-ն կառուցում է **mission graph**՝ փոքր graph grammar-ով. lock-and-key ցիկլեր, ability gate-եր, shortcut loop-եր և ընտրովի ճյուղեր։ Այն ներդրվում է բաժինների մեջ (`mission/`), և item-state graph-ը ապացուցում է, որ ամեն կողպեք հասանելի է միայն իր բանալուց հետո՝ առանց soft-lock-ի։ «Բանալին քարանձավում է, դուռը՝ ամրոցում» կառուցվածքային ստուգում է fidelity report-ում։

### Endless chunk-եր

`POST /chunks`-ը world-ին կցում է seeded noise chunk-եր, մեկ LDtk level յուրաքանչյուր chunk-ի համար։ Նույն seed-ը և chunk-ի համարը միշտ տալիս են նույն chunk-ը, իսկ հարևանները միանում են ֆիքսված seam բացվածքներով։ Յուրաքանչյուր chunk ստուգվում է, ինչպես նաև soft-lock-ի համար, ինքնուրույն և իր seam-երի միջով։

### Pattern library

GLDtk-ն կարող է LLM-ին ցույց տալ իրական մակարդակի հատվածներ որպես օրինակներ (retrieval-augmented generation, RAG)։ **Միայն լիցենզավորված աղբյուրներ.** LDtk-ի սեփական MIT լիցենզիայով նմուշային project-ները, գումարած քո մակարդակները և GLDtk-ի այն մակարդակները, որոնք դու հաստատել ես (պահել ես, չես undo արել)։ Առևտրային խաղերի մակարդակներ չեն օգտագործվում։ Յուրաքանչյուր հատված կտրվում է սենյակի կամ set-piece-ի, ստուգվում է (soft-lock առաջացնող pattern-ները մերժվում են), նշագրվում է մեկ LLM call-ով, embed է արվում և պահվում է տեղական ինդեքսում app data թղթապանակում (`GLDTK_PATTERNS_DIR`-ը փոխարինում է այն)։ Generate-ի կամ edit-ի ժամանակ լավագույն համընկնումները մտնում են prompt-ի մեջ, և պատասխանը թվարկում է, թե որ pattern-ներն են օգտագործվել։ Պահվում է միայն layout-ը, երբեք՝ art-ը։

```bash
python -m patterns.build --dry-run                          # ingest only, no LLM call
python -m patterns.build --provider gemini --max-calls 40   # label and embed
```

### Design memory

GLDtk-ն սովորում է քո ճաշակը այն բանից, ինչ դու փոխում ես. նրա արդյունքի ձեռքով խմբագրումներ, undo-ներ, ընդունված edit-ներ, Game Profile-ի փոփոխություններ։ Տեղական իրադարձությունների մատյանը վերածվում է կարճ նախասիրության կանոնների («նախընտրում է 2–3 թշնամի սենյակում»), որոնք մտնում են հետագա prompt-ների մեջ և ճշգրտում են դետերմինիստական պարամետրերը։ Այն **տեղական է և cross-project**, պահվում է `~/Library/Application Support/GLDtk/memory/`-ում (`GLDTK_MEMORY_DIR`-ը փոխարինում է այն) և երբեք չի վերբեռնվում, բացի կանոնի կարճ տեքստից՝ LLM prompt-ի ներսում։ Sidebar-ի **Memory** panel-ը (կամ `GET/PUT/DELETE /memory`) թույլ է տալիս դիտել, խմբագրել, ջնջել կամ reset անել կանոնները, կամ անջատել memory-ն։

---

<a id="repository-layout"></a>

## Repository-ի կառուցվածք

```
server.py         FastAPI backend (endpoints listed below)
api/              request and response models, art routes
spec/             LevelSpec schema, validation, entity catalog
llm/              prompts, LLMController, patcher, providers/ (gemini, anthropic, ollama, openai, fallback)
pipeline/         build_level (spec → verified level), completeness, complexity loop, worlds, chunks.py
layout/           spec layout, comfort, densify, rock fill and noise, route, soft-locks, grid
                  verification and repair, LDtk export and reader, world.py
layout/terrain/   structure generators: corridor, parkour, labyrinth, arena, shaft, hub, open
mission/          mission graph grammar, space embedding, item-state verification
patterns/         pattern library: ingest, label, embed, retrieve, store (RAG)
memory/           design memory: event log, hand-edit diffs, analysis, preferences
symbolic/         graph schema, physics oracle, grid → graph extractor
game/             Game Profile (gldtk.game.json)
fidelity/         requirement checker, report, deterministic fill
edit/             regional editing, seams, stitching, version history
aesthetic/        themes, autotiling, sprites, asset library loader
assets/           CC0 tile themes and sprites (manifest.json)
art/              optional AI theme/sprite generation (Gemini image models; needs a paid key)
eval/             evaluation harness and prompt suite
packaging/        PyInstaller spec for the bundled backend
scripts/          build_assets.py, build_mac_app.sh
tests/            offline tests (fake LLM with recorded responses)
ldtk-src/         LDtk fork with the GLDtk sidebar (separate git repository)
example.py        generate a level from the command line
```

---

<a id="without-the-app-python-and-http"></a>

## Առանց ծրագրի. Python և HTTP

```bash
python example.py "A frozen mountain climb with 3 checkpoints and a yeti at the top"
python example.py --provider ollama --model qwen2.5:14b "a small cave with a spike pit"
```

`example.py`-ն օգտագործում է նույն կոդային ուղին, ինչ sidebar-ը, և գրում է `example_out/level.ldtk`։

HTTP-ով, երբ backend-ն աշխատում է.

```bash
curl -s http://127.0.0.1:8765/generate -H 'Content-Type: application/json' -d '{
  "description": "a forest with 5 coins and a slime",
  "output_path": "/absolute/path/level.ldtk"
}'
```

Edit-ները ստանում են նույն `.ldtk` ուղին. rect-երը tile-երով են, 0-ական տողը վերևում է։ Ամեն generate և edit հարցում ընդունում է `provider` և `model`։

| Endpoint | Նպատակ |
|---|---|
| `POST /generate` | կառուցել մակարդակ կամ world (`description`, `output_path`, `seed`, `complexity`, `fill`, `levels`, `level`, `job_id`, `densify`, …) |
| `POST /edit/region` | `{path, rect: {col,row,w,h}, prompt}` վերագեներացնել ուղղանկյունը |
| `POST /edit/section` | `{path, section_id, prompt}`. կառուցվածքի փոփոխությունը LLM call չի պահանջում |
| `POST /edit/entities` | `{path, prompt, rect?, entity_iids?}` միայն entity-ների edit |
| `POST /edit/locate` | `{path, text}` գտնել նկարագրության նշած տարածքը |
| `POST /undo`, `GET /history` | հետ գնալ snapshot-ներով. թվարկել դրանք |
| `GET /route`, `POST /route` | ստուգված երթուղի և soft-lock հատվածներ (POST-ը ձեռքով փոփոխություններից հետո նորից հանում է) |
| `GET /difficulty` | բաժին առ բաժին դժվարության կոր |
| `POST /chunks` | `{path, count, seed, theme?, orientation?}` կցել endless chunk-եր |
| `GET /progress`, `POST /cancel` | կենդանի քայլեր և cancel `job_id`-ի համար |
| `GET /memory`, `PUT /memory`, `DELETE /memory`, `POST /memory/scan` | design memory. դիտել, խմբագրել, reset, ֆայլը սկանավորել ձեռքով փոփոխությունների համար |
| `GET /profile`, `PUT /profile` | Game Profile |
| `GET /providers`, `GET /themes` | հասանելի LLM-ներ. asset theme-եր |
| `GET /settings`, `POST /settings` | API key և տեղական fallback (օգտագործվում է Mac ծրագրի կողմից) |
| `GET /health` | liveness |
| `POST /art/theme`, `POST /art/sprite`, `GET /art/library` | ընտրովի AI art |

Environment variable-ներ. `GEMINI_API_KEY` (`.env`), `GLDTK_LOCAL_FALLBACK`, `GLDTK_MEMORY_DIR`, `GLDTK_PATTERNS_DIR`, `GLDTK_CONFIG_DIR` (որտեղ Mac ծրագիրը պահում է իր կարգավորումները), `GLDTK_HOST`, `GLDTK_PORT` և `GLDTK_FAKE_LLM` (միայն թեստերի համար. պատրաստի LLM պատասխաններ)։

---

<a id="evaluation"></a>

## Գնահատում

`python -m eval.run`-ը իրական pipeline-ով աշխատեցնում է 27 prompt-ից բաղկացած փաթեթ և հաշվետվություն է տալիս fidelity-ի, խաղալիության, կրկնությունների և ժամանակի մասին՝ ըստ provider-ի և մոդելի։ Տես [eval/README.md](eval/README.md)։ Առաջին իրական գործարկումը (Gemini, 8 prompt, 2026-10-15) ստացավ **100% խաղալի և 100% fidelity** (`eval/results/gemini-2026-10-15/report.md`)։ Նրա պատասխանները գրանցված են, ուստի գործարկումը կարելի է կրկնել offline՝ `--replay eval/recordings/gemini-2026-10-15`-ով։

---

<a id="tests"></a>

## Թեստեր

```bash
source .venv/bin/activate
pytest                                       # offline: fake LLM with recorded responses
pytest -m e2e tests/e2e/test_gemini.py -s    # one real Gemini call (uses free-tier quota)
```

---

<a id="troubleshooting"></a>

## Խնդիրների լուծում

- **«Cannot reach GLDtk server».** backend-ը չի աշխատում։ Գործարկիր այն `./start.sh`-ով կամ `python server.py`-ով։
- **Gemini 429 / 503 / 404.** տես [provider-ների բաժինը](#llm-providers-and-the-local-fallback)։ Ներբեռնիր տեղական fallback մոդելը (`ollama pull`) կամ sidebar-ում ընտրիր այլ provider։
- **Ollama «model not found».** գործարկիր `ollama pull <model>` և ստուգիր `ollama list`։
- **Generate-ը կամ edit-ը ձախողվում է «not verified» կամ soft-lock սխալով.** մակարդակը չհաջողվեց ապացուցել անցանելի ընթացիկ Game Profile-ով, ուստի ոչինչ չգրվեց։ Փորձիր նորից (այլ seed-ով) կամ պարզեցրու հարցումը։
- **LDtk-ում tile-երը դատարկ են.** theme sheet-երը պատճենվում են `.ldtk`-ի կողքի `gldtk_assets/`-ում։ Պահիր այդ թղթապանակը project-ի հետ միասին։

---

<a id="build-the-mac-app"></a>

## Mac ծրագրի կառուցում

`scripts/build_mac_app.sh`-ը կառուցում է չստորագրված, Apple Silicon-ի համար `GLDtk.app` և `.dmg`, որոնք ներառում են backend-ը (գործարկելու համար terminal, venv կամ Node պետք չեն).

1. `server.py`-ի PyInstaller one-folder build (`packaging/gldtk-server.spec`) դեպի `packaging/dist/gldtk-server`.
2. Haxe release build-եր (`renderer.hxml`, `main.hxml`).
3. `electron-builder --mac dmg --arm64`՝ `CSC_IDENTITY_AUTO_DISCOVERY=false`-ով (առանց code signing-ի և notarization-ի)։

`.dmg`-ն հայտնվում է `ldtk-src/app/redist/`-ում։ Ծրագիրը գործարկում է `gldtk-server`-ը 8765 port-ով (կամ ազատ port-ով) և փակվելիս կանգնեցնում է այն։ Առաջին գործարկման ժամանակ sidebar-ում մուտքագրիր Gemini API key-ը. այն պահվում է `~/Library/Application Support/GLDtk/.env`-ում։ Նկատի ունեցիր, որ release renderer build-ը վերագրում է `ldtk-src/app/assets/js/renderer.js`-ը. զարգացման համար նորից գործարկիր `renderer.debug.hxml`-ը։ `gldtk-server --check`-ը smoke-test է անում bundle-ը։
