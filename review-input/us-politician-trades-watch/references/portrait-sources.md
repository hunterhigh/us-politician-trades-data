# Portrait sources for the internal review demo

The images in `assets/portraits/` are identity references from official U.S. government websites. They are embedded into the generated HTML so the prototype remains portable.

| Person | Local asset | Official source |
|---|---|---|
| Donald Trump | `donald-trump.png` | [White House biography](https://www.whitehouse.gov/administration/donald-j-trump/) |
| Thomas H Tuberville | `thomas-h-tuberville.jpg` | [Official Senate biography](https://www.tuberville.senate.gov/about/) |
| Markwayne Mullin | `markwayne-mullin.jpg` | [White House Cabinet](https://www.whitehouse.gov/administration/cabinet/) |
| Scott A Kupor | `scott-a-kupor.jpg` | [Office of Personnel Management biography](https://www.opm.gov/about-us/who-we-are/opm-director-scott-kupor/) |
| Nancy Pelosi | `nancy-pelosi.jpg` | [Official House biography](https://pelosi.house.gov/biography) |
| Scott H. Peters | `scott-h-peters.jpg` | [Official House biography](https://scottpeters.house.gov/about) |
| Steve Cohen | `steve-cohen.jpg` | [House Clerk member record](https://clerk.house.gov/Members/C001068) |
| Alan Armstrong | initials fallback | [Official Senate biography](https://www.armstrong.senate.gov/about/) |
| April McClain Delaney | `april-mcclain-delaney.jpg` | [Official House biography](https://mcclaindelaney.house.gov/about) |
| Daniel Crenshaw | `daniel-crenshaw.jpg` | [Official House portrait page](https://crenshaw.house.gov/official-portrait) |
| Laurel Lee | `laurel-lee.jpg` | [Official House biography](https://laurellee.house.gov/about) |
| Jared Isaacman | `jared-isaacman.jpg` | [NASA biography](https://www.nasa.gov/people/jared-isaacman/) |
| Tony Wied | `tony-wied.jpg` | [House Clerk member record](https://clerk.house.gov/Members/W000829) |
| Frank J Bisignano | initials fallback | [Social Security Administration biography](https://www.ssa.gov/agency/commissioner/) |
| Ted Cruz | `ted-cruz.jpg` | [Official Senate press kit](https://www.cruz.senate.gov/newsroom/press-kit) |
| Alexandria Ocasio-Cortez | `alexandria-ocasio-cortez-bioguide.jpg` | [Biographical Directory of the United States Congress](https://bioguide.congress.gov/search/bio/O000172) |
| Bernie Sanders | `bernie-sanders.jpg` | [Official Senate biography](https://www.sanders.senate.gov/about-bernie/) |
| Ro Khanna | `ro-khanna.jpg` | [Official House biography](https://khanna.house.gov/about/about-rep-khanna) |

Portraits and names are real-world identity materials. Priority rank, priority count, group placement, canonical transaction, holding, market, filing-status, and source-health values remain fictional demo fixtures. Donald Trump's separately labeled frozen profile reference is the only third-party reference snapshot; it is not canonical evidence and never enters the Dashboard's 30/90-day aggregates.

In the priority cards the portrait is a small inline thumbnail inside the header row, not a half-width panel, and it never displaces the disclosed purchase and sale figures that are the card's actual content. `demo_priority_count` is retained only as validated fixture data and has no UI outlet.
