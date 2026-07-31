"""
歷代 MotoGP 車手 + 歷代 NASCAR 車手完整資料
涵蓋範圍：
  MotoGP (500cc+MotoGP): 1949-2026 所有世界冠軍 + 傳奇車手
  NASCAR Cup Series:    1949-2024 所有冠軍 + 傳奇車手
"""

# ═══════════════════ MotoGP HISTORY ═══════════════════
# 500cc World Championship (1949-2001) + MotoGP (2002-present)
# 欄位：(id, full_name, short, country_iso, manufacturer, titles, wins,
#        career_span, ovr_base, element, accent, era)
# 元素分派：fire=攻擊型/拚戰、water=技巧/冷靜、wind=速度/賽道大師、earth=耐力/防禦

MOTOGP_LEGENDS = [
    # ─── 神級 OVR 94+（8+ 冠軍）───
    ("ago",  "Giacomo Agostini",    "Agostini",     "IT", "MV Agusta",   15, 122, "1964-1977", 99, "fire",  "#B71C1C", "golden"),
    ("nieto","Ángel Nieto",         "Nieto",        "ES", "Derbi",       13, 90,  "1969-1986", 97, "wind",  "#FFD600", "golden"),
    ("rossi","Valentino Rossi",     "Rossi",        "IT", "Yamaha",      9,  115, "1996-2021", 98, "fire",  "#F9CA24", "modern"),
    ("hail", "Mike Hailwood",       "Hailwood",     "GB", "MV Agusta",   9,  76,  "1958-1967", 96, "wind",  "#003087", "golden"),
    ("ubb",  "Carlo Ubbiali",       "Ubbiali",      "IT", "MV Agusta",   9,  39,  "1949-1960", 94, "water", "#1B5E20", "golden"),
    ("mm93", "Marc Márquez",        "M.Márquez",    "ES", "Honda",       8,  94,  "2013-2026", 97, "fire",  "#E8002D", "modern"),
    ("sur",  "John Surtees",        "Surtees",      "GB", "MV Agusta",   7,  38,  "1956-1960", 93, "wind",  "#003087", "golden"),
    ("read", "Phil Read",           "Read",         "GB", "Yamaha",      7,  52,  "1961-1976", 92, "water", "#003087", "classic"),
    ("duke", "Geoff Duke",          "Duke",         "GB", "Norton",      6,  33,  "1949-1959", 92, "wind",  "#003087", "golden"),

    # ─── Myth OVR 90-93（5-6 冠軍）───
    ("lor",  "Jorge Lorenzo",       "Lorenzo",      "ES", "Yamaha",      5,  68,  "2002-2019", 93, "wind",  "#0066CC", "modern"),
    ("doo",  "Mick Doohan",         "Doohan",       "AU", "Honda",       5,  54,  "1989-1999", 93, "fire",  "#012169", "classic"),
    ("law",  "Eddie Lawson",        "Lawson",       "US", "Yamaha",      4,  31,  "1983-1992", 91, "water", "#3C3B6E", "classic"),
    ("rain", "Wayne Rainey",        "Rainey",       "US", "Yamaha",      3,  24,  "1984-1993", 92, "fire",  "#3C3B6E", "classic"),
    ("roberts","Kenny Roberts Sr.", "K.Roberts",    "US", "Yamaha",      3,  24,  "1978-1983", 92, "wind",  "#3C3B6E", "classic"),
    ("spen", "Freddie Spencer",     "Spencer",      "US", "Honda",       3,  27,  "1980-1988", 91, "fire",  "#3C3B6E", "classic"),
    ("hail_sr","Bruno Ruffo",       "Ruffo",        "IT", "Moto Guzzi",  3,  7,   "1949-1953", 86, "earth", "#B71C1C", "golden"),
    ("prov", "Umberto Masetti",     "Masetti",      "IT", "Gilera",      2,  4,   "1949-1955", 84, "water", "#B71C1C", "golden"),
    ("anderson","Hugh Anderson",    "Anderson",     "NZ", "Suzuki",      4,  25,  "1961-1966", 90, "water", "#000000", "golden"),
    ("ivy",  "Bill Ivy",            "Ivy",          "GB", "Yamaha",      1,  21,  "1965-1969", 87, "wind",  "#003087", "classic"),
    ("read_sr","Jim Redman",        "Redman",       "ZW", "Honda",       6,  45,  "1961-1966", 91, "wind",  "#006400", "golden"),

    # ─── Legend OVR 85-89（2-4 冠軍 + 名將）───
    ("sh",   "Barry Sheene",        "Sheene",       "GB", "Suzuki",      2,  23,  "1970-1984", 90, "fire",  "#003087", "classic"),
    ("ston", "Casey Stoner",        "Stoner",       "AU", "Ducati",      2,  38,  "2006-2012", 93, "water", "#CC0000", "modern"),
    ("schwantz","Kevin Schwantz",   "Schwantz",     "US", "Suzuki",      1,  25,  "1986-1995", 90, "fire",  "#3C3B6E", "classic"),
    ("gard", "Wayne Gardner",       "Gardner",      "AU", "Honda",       1,  18,  "1983-1992", 88, "earth", "#012169", "classic"),
    ("pbias","Max Biaggi",          "Biaggi",       "IT", "Yamaha",      4,  42,  "1991-2005", 89, "wind",  "#B71C1C", "classic"),
    ("kr2",  "Kenny Roberts Jr.",   "K.Roberts Jr", "US", "Suzuki",      1,  8,   "1996-2007", 86, "wind",  "#3C3B6E", "modern"),
    ("criv", "Àlex Crivillé",       "Crivillé",     "ES", "Honda",       1,  20,  "1987-2002", 87, "earth", "#FFD600", "classic"),
    ("hay",  "Nicky Hayden",        "Hayden",       "US", "Honda",       1,  3,   "2003-2015", 86, "earth", "#3C3B6E", "modern"),
    ("pb1",  "Francesco Bagnaia",   "Bagnaia",      "IT", "Ducati",      2,  27,  "2019-2026", 92, "fire",  "#CC0000", "modern"),
    ("fq20", "Fabio Quartararo",    "Quartararo",   "FR", "Yamaha",      1,  11,  "2019-2026", 89, "wind",  "#0055A4", "modern"),
    ("mir",  "Joan Mir",            "Mir",          "ES", "Suzuki",      1,  1,   "2019-2026", 86, "water", "#FFD600", "modern"),
    ("mart", "Jorge Martín",        "Martín",       "ES", "Ducati",      1,  9,   "2021-2026", 91, "fire",  "#CC0000", "modern"),
    ("pedrosa","Dani Pedrosa",      "Pedrosa",      "ES", "Honda",       0,  31,  "2001-2018", 89, "water", "#E8002D", "modern"),
    ("dovi", "Andrea Dovizioso",    "Dovizioso",    "IT", "Ducati",      0,  15,  "2002-2022", 87, "earth", "#CC0000", "modern"),
    ("vinales","Maverick Viñales",  "Viñales",      "ES", "Aprilia",     0,  9,   "2013-2026", 85, "wind",  "#006633", "modern"),
    ("aleix","Aleix Espargaró",     "A.Espargaró",  "ES", "Aprilia",     0,  4,   "2004-2024", 83, "earth", "#006633", "modern"),
    ("pol",  "Pol Espargaró",       "P.Espargaró",  "ES", "KTM",         0,  1,   "2004-2024", 81, "fire",  "#FF6600", "modern"),
    ("iann", "Andrea Iannone",      "Iannone",      "IT", "Suzuki",      0,  1,   "2005-2019", 80, "fire",  "#FFD600", "modern"),
    ("peti", "Danilo Petrucci",     "Petrucci",     "IT", "Ducati",      0,  2,   "2012-2022", 79, "earth", "#CC0000", "modern"),
    ("cr29", "Johann Zarco",        "Zarco",        "FR", "Ducati",      0,  2,   "2015-2026", 83, "wind",  "#0055A4", "modern"),
    ("miller","Jack Miller",        "Miller",       "AU", "KTM",         0,  4,   "2015-2026", 83, "fire",  "#012169", "modern"),
    ("binder","Brad Binder",        "Binder",       "ZA", "KTM",         0,  4,   "2016-2026", 86, "fire",  "#FF6600", "modern"),
    ("bezz", "Marco Bezzecchi",     "Bezzecchi",    "IT", "Aprilia",     0,  4,   "2022-2026", 87, "wind",  "#006633", "modern"),
    ("acosta","Pedro Acosta",       "Acosta",       "ES", "KTM",         0,  0,   "2024-2026", 88, "fire",  "#FF6600", "modern"),
    ("diggia","Fabio Di Giannantonio","DiGiannantonio","IT","Ducati",    0,  2,   "2022-2026", 84, "water", "#CC0000", "modern"),
    ("am73", "Álex Márquez",        "A.Márquez",    "ES", "Ducati",      0,  2,   "2020-2026", 84, "water", "#CC0000", "modern"),
    ("mari", "Luca Marini",         "Marini",       "IT", "Honda",       0,  0,   "2020-2026", 80, "water", "#E8002D", "modern"),

    # ─── Rare OVR 78-84 ───
    ("baz",  "Loris Baz",           "Baz",          "FR", "Aprilia",     0,  0,   "2015-2018", 76, "fire",  "#0055A4", "modern"),
    ("redd", "Scott Redding",       "Redding",      "GB", "Ducati",      0,  0,   "2013-2018", 77, "water", "#003087", "modern"),
    ("simon","Tito Rabat",          "Rabat",        "ES", "Ducati",      0,  0,   "2016-2020", 76, "earth", "#CC0000", "modern"),
    ("abrah","Karel Abraham",       "Abraham",      "CZ", "Ducati",      0,  0,   "2011-2019", 74, "water", "#D7141A", "modern"),
    ("bh",   "Cal Crutchlow",       "Crutchlow",    "GB", "LCR Honda",   0,  3,   "2011-2020", 82, "earth", "#E8002D", "modern"),
    ("smith","Bradley Smith",       "Smith",        "GB", "Aprilia",     0,  0,   "2013-2020", 77, "earth", "#006633", "modern"),
    ("kallio","Mika Kallio",        "Kallio",       "FI", "KTM",         0,  0,   "2006-2019", 76, "water", "#FF6600", "modern"),
    ("elias","Toni Elías",          "Elías",        "ES", "Honda",       0,  1,   "2001-2012", 80, "fire",  "#E8002D", "modern"),
    ("cheka","Carlos Checa",        "Checa",        "ES", "Ducati",      0,  2,   "1993-2011", 82, "earth", "#CC0000", "classic"),
    ("capi", "Loris Capirossi",     "Capirossi",    "IT", "Ducati",      3,  29,  "1990-2011", 88, "fire",  "#CC0000", "classic"),
    ("gib",  "Sete Gibernau",       "Gibernau",     "ES", "Honda",       0,  9,   "1992-2006", 85, "wind",  "#E8002D", "classic"),
    ("tamada","Makoto Tamada",      "Tamada",       "JP", "Honda",       0,  2,   "2000-2008", 79, "water", "#BC002D", "modern"),
    ("naka", "Tadayuki Okada",      "Okada",        "JP", "Honda",       0,  4,   "1994-2000", 83, "water", "#BC002D", "classic"),
    ("kosh", "Shinichi Itoh",       "Itoh",         "JP", "Honda",       0,  0,   "1988-2003", 76, "earth", "#BC002D", "classic"),
    ("shobert","Bubba Shobert",     "Shobert",      "US", "Honda",       0,  1,   "1988-1989", 80, "fire",  "#3C3B6E", "classic"),
    ("mackenzie","Niall Mackenzie", "Mackenzie",    "GB", "Yamaha",      0,  0,   "1987-1993", 77, "water", "#003087", "classic"),
    ("hasl", "Leon Haslam",         "Haslam",       "GB", "Honda",       0,  0,   "2001-2015", 77, "earth", "#003087", "modern"),
    ("mam",  "Randy Mamola",        "Mamola",       "US", "Yamaha",      0,  13,  "1979-1992", 86, "wind",  "#3C3B6E", "classic"),
    ("uncini","Franco Uncini",      "Uncini",       "IT", "Suzuki",      1,  5,   "1976-1985", 86, "water", "#B71C1C", "classic"),
    ("lucchinelli","Marco Lucchinelli","Lucchinelli","IT","Suzuki",      1,  6,   "1975-1984", 86, "fire",  "#B71C1C", "classic"),
    ("ros",  "Jarno Saarinen",      "Saarinen",     "FI", "Yamaha",      1,  15,  "1970-1973", 88, "wind",  "#003580", "classic"),
    ("pas",  "Giacomo Pasolini",    "Pasolini",     "IT", "Benelli",     0,  14,  "1964-1973", 83, "fire",  "#B71C1C", "classic"),
    ("ferrari","Virginio Ferrari",  "Ferrari",      "IT", "Suzuki",      0,  2,   "1976-1983", 80, "fire",  "#B71C1C", "classic"),
    ("mort", "Kork Ballington",     "Ballington",   "ZA", "Kawasaki",    4,  31,  "1976-1982", 89, "water", "#006400", "classic"),
    ("andy", "Anton Mang",          "Mang",         "DE", "Kawasaki",    5,  42,  "1976-1988", 90, "earth", "#000000", "classic"),
    ("nowa", "Walter Villa",        "Villa",        "IT", "H-D",         4,  24,  "1969-1982", 88, "wind",  "#FF6600", "classic"),
    ("kb",   "Kel Carruthers",      "Carruthers",   "AU", "Benelli",     1,  7,   "1966-1971", 85, "wind",  "#012169", "golden"),
    ("read2","Renzo Pasolini",      "R.Pasolini",   "IT", "Aermacchi",   0,  11,  "1962-1973", 82, "fire",  "#B71C1C", "classic"),
]

# 製造商顏色
MOTOGP_MFR_COLOR = {
    "MV Agusta":  "#C8102E", "Honda":     "#E8002D", "Yamaha":   "#003087",
    "Suzuki":     "#003DA5", "Ducati":    "#CC0000", "Aprilia":  "#006633",
    "KTM":        "#FF6600", "Norton":    "#2B2B2B", "Gilera":   "#B71C1C",
    "Moto Guzzi": "#CC0000", "Derbi":     "#FFD600", "Kawasaki": "#006400",
    "Benelli":    "#006600", "LCR Honda": "#E8002D", "Aermacchi":"#C62828",
    "H-D":        "#FF6600",
}


# ═══════════════════ NASCAR HISTORY ═══════════════════
# NASCAR Cup Series 1949-2024 所有冠軍 + 傳奇車手
# 欄位同 MotoGP

NASCAR_LEGENDS = [
    # ─── 神級 OVR 95+（7 冠王三巨頭）───
    ("petty",  "Richard Petty",      "The King",     "US", "Plymouth/Chrysler", 7, 200, "1958-1992", 99, "fire",  "#1E64C8", "golden"),
    ("earn",   "Dale Earnhardt",     "The Intimidator","US","Chevrolet",       7, 76,  "1975-2001", 98, "fire",  "#000000", "classic"),
    ("jj",     "Jimmie Johnson",     "Seven-Time",   "US", "Chevrolet",        7, 83,  "2001-2020", 97, "water", "#1E64C8", "modern"),

    # ─── Myth OVR 90-94（3-4 冠軍 + 傳奇）───
    ("gordon", "Jeff Gordon",        "Rainbow Warrior","US","Chevrolet",      4, 93,  "1992-2015", 95, "wind",  "#FFD700", "modern"),
    ("yarb",   "Cale Yarborough",    "Yarborough",   "US", "Chevrolet",        3, 83,  "1957-1988", 93, "fire",  "#FFD700", "classic"),
    ("dw",     "Darrell Waltrip",    "Jaws",         "US", "Chevrolet",        3, 84,  "1972-2000", 93, "wind",  "#C8AA2B", "classic"),
    ("pearson","David Pearson",      "Silver Fox",   "US", "Ford",             3, 105, "1960-1986", 94, "water", "#003478", "classic"),
    ("stewart","Tony Stewart",       "Smoke",        "US", "Chevrolet",        3, 49,  "1999-2016", 93, "fire",  "#F58025", "modern"),
    ("logano", "Joey Logano",        "Sliced Bread", "US", "Ford",             3, 37,  "2008-2026", 92, "fire",  "#003478", "modern"),
    ("lpetty", "Lee Petty",          "L.Petty",      "US", "Plymouth",         3, 54,  "1949-1964", 90, "earth", "#1E64C8", "golden"),

    # ─── Legend OVR 85-89（1-2 冠軍 + 名將）───
    ("kb",     "Kyle Busch",         "Rowdy",        "US", "Chevrolet",        2, 63,  "2004-2026", 91, "fire",  "#EB0A1E", "modern"),
    ("tlab",   "Terry Labonte",      "T.Labonte",    "US", "Chevrolet",        2, 22,  "1978-2014", 87, "earth", "#1E64C8", "classic"),
    ("bbaker", "Buck Baker",         "Buck Baker",   "US", "Chevrolet",        2, 46,  "1949-1976", 86, "earth", "#000000", "golden"),
    ("hthomas","Herb Thomas",        "Thomas",       "US", "Hudson",           2, 48,  "1949-1956", 87, "fire",  "#B71C1C", "golden"),
    ("tflock", "Tim Flock",          "Flock",        "US", "Hudson",           2, 39,  "1949-1961", 86, "wind",  "#B71C1C", "golden"),
    ("njarr",  "Ned Jarrett",        "N.Jarrett",    "US", "Ford",             2, 50,  "1953-1966", 86, "earth", "#003478", "golden"),
    ("rexford","Bill Rexford",       "Rexford",      "US", "Oldsmobile",       1, 1,   "1949-1953", 80, "wind",  "#556B2F", "golden"),
    ("redbyron","Red Byron",         "Red Byron",    "US", "Oldsmobile",       1, 2,   "1949-1951", 82, "fire",  "#C62828", "golden"),
    ("weath",  "Joe Weatherly",      "Weatherly",    "US", "Pontiac",          2, 25,  "1952-1964", 85, "wind",  "#CC5500", "golden"),
    ("white",  "Rex White",          "Rex White",    "US", "Chevrolet",        1, 28,  "1956-1964", 84, "water", "#C8AA2B", "golden"),
    ("roberts","Fireball Roberts",   "Fireball",     "US", "Pontiac",          0, 33,  "1950-1964", 87, "fire",  "#CC5500", "golden"),
    ("turner", "Curtis Turner",      "Turner",       "US", "Chevrolet",        0, 17,  "1949-1968", 83, "fire",  "#000000", "golden"),
    ("lorenzen","Fred Lorenzen",     "Lorenzen",     "US", "Ford",             0, 26,  "1956-1972", 84, "wind",  "#003478", "golden"),
    ("bparsons","Benny Parsons",     "B.Parsons",    "US", "Chevrolet",        1, 21,  "1964-1988", 86, "earth", "#8B0000", "classic"),
    ("bonnett", "Neil Bonnett",      "Bonnett",      "US", "Chevrolet",        0, 18,  "1974-1993", 82, "earth", "#000080", "classic"),
    ("pearson2","Bobby Allison",     "B.Allison",    "US", "Buick",            1, 84,  "1961-1988", 92, "water", "#CC0000", "classic"),
    ("dallison","Donnie Allison",    "D.Allison",    "US", "Hawaiian Tropic",  0, 10,  "1966-1988", 80, "wind",  "#FFA500", "classic"),
    ("martin",  "Mark Martin",       "Martin",       "US", "Ford",             0, 40,  "1981-2013", 90, "water", "#003478", "classic"),
    ("bodine",  "Geoff Bodine",      "Bodine",       "US", "Chevrolet",        0, 18,  "1979-2011", 81, "wind",  "#C8AA2B", "classic"),
    ("wallace", "Rusty Wallace",     "Wallace",      "US", "Pontiac",          1, 55,  "1980-2005", 89, "fire",  "#2E2E2E", "classic"),
    ("kulwicki","Alan Kulwicki",     "Kulwicki",     "US", "Ford",             1, 5,   "1985-1993", 85, "water", "#003478", "classic"),
    ("belliott","Bill Elliott",      "Awesome Bill", "US", "Ford",             1, 44,  "1976-2012", 91, "wind",  "#CC0000", "classic"),
    ("djarrett","Dale Jarrett",      "D.Jarrett",    "US", "Ford",             1, 32,  "1984-2008", 89, "earth", "#003478", "classic"),
    ("blabonte","Bobby Labonte",     "B.Labonte",    "US", "Pontiac",          1, 21,  "1991-2016", 87, "water", "#EB0A1E", "classic"),
    ("kenseth", "Matt Kenseth",      "Kenseth",      "US", "Ford",             1, 39,  "1998-2018", 88, "earth", "#003478", "modern"),
    ("kurtbusch","Kurt Busch",       "K.Busch",      "US", "Ford",             1, 34,  "2000-2022", 87, "fire",  "#003478", "modern"),
    ("brad",    "Brad Keselowski",   "Bad Brad",     "US", "Ford",             1, 37,  "2008-2026", 89, "fire",  "#003478", "modern"),
    ("truex",   "Martin Truex Jr.",  "Truex Jr.",    "US", "Toyota",           1, 34,  "2004-2024", 89, "water", "#EB0A1E", "modern"),
    ("blaney",  "Ryan Blaney",       "Blaney",       "US", "Ford",             1, 15,  "2014-2026", 88, "fire",  "#003478", "modern"),
    ("larson",  "Kyle Larson",       "Larson",       "US", "Chevrolet",        1, 33,  "2013-2026", 93, "wind",  "#FFD700", "modern"),

    # ─── 其他 NASCAR Modern Era 冠軍候選人與熱門名將 OVR 80-88 ───
    ("djr",     "Dale Earnhardt Jr.","Junior",       "US", "Chevrolet",        0, 26,  "1999-2017", 88, "fire",  "#EB0A1E", "modern"),
    ("hamlin",  "Denny Hamlin",      "Hamlin",       "US", "Toyota",           0, 58,  "2005-2026", 91, "fire",  "#EB0A1E", "modern"),
    ("elliott", "Chase Elliott",     "C.Elliott",    "US", "Chevrolet",        1, 19,  "2015-2026", 89, "wind",  "#003478", "modern"),
    ("bell",    "Christopher Bell",  "Bell",         "US", "Toyota",           0, 12,  "2020-2026", 87, "fire",  "#EB0A1E", "modern"),
    ("byron",   "William Byron",     "Byron",        "US", "Chevrolet",        0, 15,  "2018-2026", 88, "wind",  "#FFD700", "modern"),
    ("chastain","Ross Chastain",     "Chastain",     "US", "Chevrolet",        0, 5,   "2017-2026", 85, "fire",  "#FFFFFF", "modern"),
    ("tgibbs",  "Ty Gibbs",          "T.Gibbs",      "US", "Toyota",           0, 1,   "2022-2026", 82, "fire",  "#EB0A1E", "modern"),
    ("cindric", "Austin Cindric",    "Cindric",      "US", "Ford",             0, 3,   "2020-2026", 82, "water", "#003478", "modern"),
    ("reddick", "Tyler Reddick",     "Reddick",      "US", "Toyota",           0, 7,   "2019-2026", 85, "wind",  "#EB0A1E", "modern"),
    ("bubba",   "Bubba Wallace",     "Bubba",        "US", "Toyota",           0, 3,   "2017-2026", 82, "fire",  "#EB0A1E", "modern"),
    ("bowman",  "Alex Bowman",       "Bowman",       "US", "Chevrolet",        0, 9,   "2014-2026", 83, "water", "#FFD700", "modern"),
    ("svg",     "Shane van Gisbergen","SVG",         "NZ", "Chevrolet",        0, 3,   "2023-2026", 87, "wind",  "#FFFFFF", "modern"),
    ("suarez",  "Daniel Suárez",     "Suárez",       "MX", "Chevrolet",        0, 2,   "2017-2026", 82, "earth", "#FFFFFF", "modern"),
    ("dillon",  "Austin Dillon",     "A.Dillon",     "US", "Chevrolet",        0, 5,   "2014-2026", 80, "earth", "#C8AA2B", "modern"),
    ("buescher","Chris Buescher",    "Buescher",     "US", "Ford",             0, 5,   "2015-2026", 82, "water", "#003478", "modern"),
    ("stenhouse","Ricky Stenhouse Jr.","Stenhouse",  "US", "Chevrolet",        0, 3,   "2013-2026", 78, "wind",  "#FFD700", "modern"),
    ("mcdowell","Michael McDowell",  "McDowell",     "US", "Ford",             0, 2,   "2008-2026", 77, "earth", "#003478", "modern"),
    ("kahne",   "Kasey Kahne",       "Kahne",        "US", "Chevrolet",        0, 18,  "2002-2018", 83, "wind",  "#EB0A1E", "modern"),
    ("harvick", "Kevin Harvick",     "Happy",        "US", "Ford",             1, 60,  "2001-2023", 93, "fire",  "#003478", "modern"),
    ("edwards", "Carl Edwards",      "Cousin Carl",  "US", "Ford",             0, 28,  "2004-2016", 88, "fire",  "#003478", "modern"),
    ("skinner", "Mike Skinner",      "Skinner",      "US", "Chevrolet",        0, 0,   "1997-2005", 75, "earth", "#C8AA2B", "classic"),
    ("burton",  "Jeff Burton",       "J.Burton",     "US", "Ford",             0, 21,  "1993-2014", 84, "water", "#003478", "classic"),
    ("ricky",   "Ricky Rudd",        "Rudd",         "US", "Ford",             0, 23,  "1975-2007", 85, "earth", "#003478", "classic"),
    ("shepherd","Morgan Shepherd",   "Shepherd",     "US", "Ford",             0, 4,   "1970-2018", 76, "earth", "#003478", "classic"),
    ("rudd2",   "Sterling Marlin",   "S.Marlin",     "US", "Chevrolet",        0, 10,  "1976-2010", 82, "water", "#C8AA2B", "classic"),
    ("gibbs2",  "Bobby Hamilton",    "B.Hamilton",   "US", "Chevrolet",        0, 4,   "1989-2006", 78, "earth", "#C8AA2B", "classic"),
    ("irvan",   "Ernie Irvan",       "Irvan",        "US", "Ford",             0, 15,  "1987-1999", 83, "fire",  "#003478", "classic"),
    ("gant",    "Harry Gant",        "Handsome Harry","US","Oldsmobile",       0, 18,  "1973-1994", 84, "wind",  "#556B2F", "classic"),
    ("rrichmond","Tim Richmond",     "Richmond",     "US", "Chevrolet",        0, 13,  "1980-1987", 85, "fire",  "#C8AA2B", "classic"),
    ("yaley",   "Davey Allison",     "Davey",        "US", "Ford",             0, 19,  "1985-1993", 87, "fire",  "#003478", "classic"),
    ("kulwicki2","Ken Schrader",     "Schrader",     "US", "Chevrolet",        0, 4,   "1984-2013", 78, "earth", "#C8AA2B", "classic"),
    ("hocevar", "Carson Hocevar",    "Hocevar",      "US", "Chevrolet",        0, 0,   "2023-2026", 78, "wind",  "#FFFFFF", "modern"),
    ("preece",  "Ryan Preece",       "Preece",       "US", "Ford",             0, 0,   "2019-2026", 76, "water", "#003478", "modern"),
    ("gragson", "Noah Gragson",      "Gragson",      "US", "Ford",             0, 0,   "2021-2026", 77, "fire",  "#003478", "modern"),
    ("jones",   "Erik Jones",        "E.Jones",      "US", "Toyota",           0, 2,   "2015-2026", 81, "water", "#EB0A1E", "modern"),
    ("almirola","Aric Almirola",     "Almirola",     "US", "Ford",             0, 3,   "2007-2023", 80, "earth", "#003478", "modern"),
    ("biffle",  "Greg Biffle",       "Biffle",       "US", "Ford",             0, 19,  "2002-2016", 84, "fire",  "#003478", "modern"),
    ("newman",  "Ryan Newman",       "Rocket Man",   "US", "Ford",             0, 18,  "2000-2021", 84, "wind",  "#003478", "modern"),
    ("ambrose", "Marcos Ambrose",    "Ambrose",      "AU", "Ford",             0, 2,   "2008-2014", 80, "wind",  "#012169", "modern"),
    ("montoya", "Juan Pablo Montoya","JPM",          "CO", "Chevrolet",        0, 2,   "2007-2013", 84, "wind",  "#FDD023", "modern"),
    ("vickers", "Brian Vickers",     "Vickers",      "US", "Toyota",           0, 3,   "2003-2016", 79, "water", "#EB0A1E", "modern"),
    ("waltrip", "Michael Waltrip",   "M.Waltrip",    "US", "Chevrolet",        0, 4,   "1985-2017", 78, "wind",  "#C8AA2B", "classic"),
    ("craven",  "Ricky Craven",      "Craven",       "US", "Ford",             0, 2,   "1991-2004", 78, "water", "#003478", "classic"),
    ("marshall","Jimmy Spencer",     "Mr.Excitement","US", "Ford",             0, 2,   "1989-2006", 78, "fire",  "#003478", "classic"),
    ("eskinner","Elliott Sadler",    "E.Sadler",     "US", "Dodge",            0, 3,   "1998-2018", 80, "water", "#FF6600", "modern"),
]

NASCAR_MFR_COLOR = {
    "Chevrolet":          "#C8AA2B", "Ford":     "#003478",
    "Toyota":             "#EB0A1E", "Dodge":    "#FF6600",
    "Plymouth":           "#1E64C8", "Plymouth/Chrysler":"#1E64C8",
    "Pontiac":            "#CC5500", "Buick":    "#B71C1C",
    "Hudson":             "#B71C1C", "Oldsmobile":"#556B2F",
    "Hawaiian Tropic":    "#FFA500",
}
