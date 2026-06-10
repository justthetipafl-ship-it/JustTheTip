Run Rscript probe_nrl_v2.R "2026" "1" > nrl_probe_output.txt 2>&1 || true
================= PROBE OUTPUT =================
============================================================
JTT NRL probe v2 — nrlR 0.1.2 — season 2026 round 1
============================================================

0. nrlR exports (so we stop guessing fetch_results / fetch_ladder):
 [1] "fetch_cd_comps"                       
 [2] "fetch_coaches"                        
 [3] "fetch_fixture"                        
 [4] "fetch_fixture_nrl"                    
 [5] "fetch_injuries_suspensions"           
 [6] "fetch_injuries_suspensions_zerotackle"
 [7] "fetch_ladder"                         
 [8] "fetch_ladder_nrl"                     
 [9] "fetch_lineups"                        
[10] "fetch_player_stats"                   
[11] "fetch_player_stats_championdata"      
[12] "fetch_results"                        
[13] "fetch_results_rugbyproject"           
[14] "fetch_team_stats_championdata"        
[15] "fetch_venues"                         

1. fetch_cd_comps()  [no args]

--- SCHEMA: competitions ---
  316 rows x 7 cols
    id                         integer    e.g. 8005
    name                       character  e.g. ANZ Championship
    regulationPeriodLength     integer    e.g. 900
    regulationPeriods          integer    e.g. 4
    rounds                     integer    e.g. 14
    season                     integer    e.g. 2009
    extraTimePeriodLength      integer    e.g. 300
  -> NRL comp '2014 NRL Premiership' id=9135
  (all comp names for reference:)
  [1] "ANZ Championship"                                       
  [2] "ANZ Finals"                                             
  [3] "ANZ Championship"                                       
  [4] "ANZ Finals"                                             
  [5] "ANZ Championship"                                       
  [6] "ANZ Finals"                                             
  [7] "ANZ Championship"                                       
  [8] "ANZ Finals"                                             
  [9] "ANZ Championship"                                       
 [10] "ANZ Finals"                                             
 [11] "ANZ Championship"                                       
 [12] "ANZ Finals"                                             
 [13] "Auckland Nines"                                         
 [14] "College Netball"                                        
 [15] "2014 NRL Premiership"                                   
 [16] "2014 NRL Finals"                                        
 [17] "2014 State of Origin"                                   
 [18] "2014 Group Championships Under 23"                      
 [19] "FAST5"                                                  
 [20] "Constellation Cup"                                      
 [21] "State Championship"                                     
 [22] "Lion Foundation National Champs"                        
 [23] "Secondary School Champs"                                
 [24] "England in Australia"                                   
 [25] "England in New Zealand"                                 
 [26] "Four Nations"                                           
 [27] "Auckland Nines"                                         
 [28] "2015 NRL Premiership"                                   
 [29] "2015 NRL Finals"                                        
 [30] "2015 Australia v NZ Test"                               
 [31] "2015 State of Origin"                                   
 [32] "2015 Country v City"                                    
 [33] "2015 Indigenous All Stars"                              
 [34] "2015 Charity Shield"                                    
 [35] "ANZ Championship"                                       
 [36] "ANZ Finals"                                             
 [37] "College Netball"                                        
 [38] "Trans-Tasman U19"                                       
 [39] "Silver Ferns Internationals"                            
 [40] "Constellation Cup"                                      
 [41] "2015 Group Championships Under 23"                      
 [42] "NWC Preliminary"                                        
 [43] "NWC Qualification"                                      
 [44] "NWC Placings"                                           
 [45] "NWC Medals"                                             
 [46] "Netball NZ National Champs"                             
 [47] "Netball NZ Secondary Schools"                           
 [48] "2016 AFL Season"                                        
 [49] "2016 AFL Finals"                                        
 [50] "ANZ Championship"                                       
 [51] "ANZ Finals"                                             
 [52] "England Roses vs Australian Diamonds"                   
 [53] "2016 NRL Country v City"                                
 [54] "2016 Australia Tests"                                   
 [55] "2016 Charity Shield"                                    
 [56] "2016 Club World Series"                                 
 [57] "2016 Indigenous All Stars"                              
 [58] "2016 Telstra NRL Premiership"                           
 [59] "2016 Telstra NRL Finals"                                
 [60] "2016 Auckland Nines"                                    
 [61] "2016 State of Origin"                                   
 [62] "2016 Beko National League"                              
 [63] "2016 Constellation Cup"                                 
 [64] "2016 International Netball Super Series"                
 [65] "2016 Taini Jamison Trophy"                              
 [66] "Netball NZ U19 Champs"                                  
 [67] "Fast5 World Series"                                     
 [68] "2016 Netball NZ Secondary School"                       
 [69] "Four Nations"                                           
 [70] "2017 Super Netball"                                     
 [71] "2017 Super Netball Finals"                              
 [72] "2017 Country v City"                                    
 [73] "Auckland Nines"                                         
 [74] "Auckland Nines Finals"                                  
 [75] "2017 AFL Womens"                                        
 [76] "2016 AFL Womens Test"                                   
 [77] "2017 Netball Quad Series - January"                     
 [78] "2017 Silver Ferns Tour of Wales"                        
 [79] "Womens AFL Test"                                        
 [80] "2017 Telstra NRL Premiership"                           
 [81] "2017 Telstra NRL Finals"                                
 [82] "2017 State of Origin"                                   
 [83] "2017 Australia v NZ"                                    
 [84] "2017 Indigenous All Stars"                              
 [85] "2017 Charity Shield"                                    
 [86] "2017 Club World Series"                                 
 [87] "2017 ANZ Premiership"                                   
 [88] "2017 ANZ Premiership Finals"                            
 [89] "NNZ Pre-Season"                                         
 [90] "AFL Womens Finals"                                      
 [91] "2017 Beko Netball League"                               
 [92] "2017 Super Club"                                        
 [93] "2017 Taini Jamison Trophy"                              
 [94] "2017 Netball Quad Series - August"                      
 [95] "2017 Constellation Cup"                                 
 [96] "2017 Fast5 World Series"                                
 [97] "2017 NZ Secondary Schools - Pool Phase 1"               
 [98] "2017 NZ Secondary Schools - Pool Phase 2"               
 [99] "2017 NZ Secondary Schools - Finals"                     
[100] "2017 Rugby League World Cup"                            
[101] "2017 Rugby League World Cup Finals"                     
[102] "2018 January Netball Quad Series"                       
[103] "AFLW 2018"                                              
[104] "AFLW Finals 2018"                                       
[105] "2018 Telstra NRL Premiership"                           
[106] "2018 Telstra NRL Finals"                                
[107] "2018 Taini Jamison Trophy"                              
[108] "2018 State of Origin"                                   
[109] "2018 Charity Shield"                                    
[110] "2018 Club World Series"                                 
[111] "2018 Super Netball"                                     
[112] "2018 Super Netball Finals"                              
[113] "2018 ANZ Premiership"                                   
[114] "2018 ANZ Premiership Finals"                            
[115] "2018 Beko Netball League"                               
[116] "2018 Constellation Cup"                                 
[117] "2018 Netball Quad Series - September"                   
[118] "2018 Super Club"                                        
[119] "2018 Super Club Finals"                                 
[120] "2019 ANZ Premiership"                                   
[121] "2019 ANZ Premiership Finals"                            
[122] "2018 Fast5 World Series"                                
[123] "2018 Pita Pit NZ Secondary Schools Netball Champs - R1" 
[124] "2018 Pita Pit NZ Secondary Schools Netball Champs - R2" 
[125] "2018 Pita Pit NZ Secondary Schools Netball Champs - Fin"
[126] "2018 Australia v NZ Test"                               
[127] "2018 Australia v Tonga Test"                            
[128] "2018 Fast5 Trials"                                      
[129] "2019 Super Netball"                                     
[130] "2019 Super Netball Finals"                              
[131] "2019 Netball Quad Series - January"                     
[132] "2019 Telstra NRL Premiership"                           
[133] "2019 Telstra NRL Finals"                                
[134] "2019 State of Origin"                                   
[135] "2019 Harvey Norman All-Stars"                           
[136] "2019 TeamGirls Cup Pool A"                              
[137] "2019 TeamGirls Cup Pool B"                              
[138] "2019 TeamGirls Cup Finals"                              
[139] "2019 World Club Challenge"                              
[140] "2019 Charity Shield"                                    
[141] "2019 Beko Netball League"                               
[142] "2019 Beko Netball League Final"                         
[143] "2019 Vitality Super League"                             
[144] "Preliminaries Stage 1 and 2"                            
[145] "Play-offs and Placings"                                 
[146] "2019 Cadbury Netball Series"                            
[147] "2019 Constellation Cup"                                 
[148] "2019 Kangaroos Tests"                                   
[149] "2019 Super Club"                                        
[150] "2019 Super Club Finals"                                 
[151] "2020 ANZ Premiership"                                   
[152] "2020 ANZ Premiership Finals"                            
[153] "2020 Super Netball"                                     
[154] "2020 Super Netball Finals"                              
[155] "2020 Nations Cup"                                       
[156] "2020 Bushfire Relief"                                   
[157] "2020 Telstra NRL Premiership"                           
[158] "2020 Telstra NRL Finals"                                
[159] "2020 State of Origin"                                   
[160] "2020 Taini Jamison Trophy"                              
[161] "2020 Cadbury Netball Series"                            
[162] "2021 Constellation Cup"                                 
[163] "2021 Telstra NRL Premiership"                           
[164] "2021 Telstra NRL Finals"                                
[165] "2021 Harvey Norman All-Stars"                           
[166] "2021 State of Origin"                                   
[167] "2021 ANZ Premiership"                                   
[168] "2021 ANZ Premiership Finals"                            
[169] "2021 Super Netball"                                     
[170] "2021 Super Netball Finals"                              
[171] "2021 National Netball League"                           
[172] "2021 National Netball League Final"                     
[173] "2021 Taini Jamison Trophy"                              
[174] "2021 Cadbury Netball Series"                            
[175] "2022 ANZ Premiership"                                   
[176] "2022 ANZ Premiership Finals"                            
[177] "2022 January Netball Quad Series"                       
[178] "2022 Super Netball"                                     
[179] "2022 Super Netball Finals"                              
[180] "2022 Harvey Norman All-Stars"                           
[181] "2022 Telstra NRL Premiership"                           
[182] "2022 Telstra NRL Finals"                                
[183] "2022 State of Origin"                                   
[184] "2022 NRLW"                                              
[185] "2022 State of Origin Womens"                            
[186] "2022 NRLW Finals"                                       
[187] "2022 #TeamGirls Cup A"                                  
[188] "2022 #TeamGirls Cup B"                                  
[189] "2022 #TeamGirls Cup Finals"                             
[190] "2022 National Netball League"                           
[191] "2022 National Netball League"                           
[192] "2022 Cadbury Netball Series"                            
[193] "2022 Australian Netball Championships"                  
[194] "2022 Australian Netball Championships Finals"           
[195] "2022B NRLW"                                             
[196] "2022B NRLW Finals"                                      
[197] "2022 Constellation Cup"                                 
[198] "2022 England Series"                                    
[199] "2022 Taini Jamison Trophy"                              
[200] "2022 NWC Regional Qualifiers"                           
[201] "2022 NWC Regional Qualifiers Finals"                    
[202] "2022 Netball NZ Open Champs"                            
[203] "2023 ANZ Premiership"                                   
[204] "2023 ANZ Premiership Finals"                            
[205] "2022 Australia Men's v NZ"                              
[206] "2022 Australia Men's v England"                         
[207] "2022 NZ Secondary Schools Champs"                       
[208] "2022 FAST5 - World Netball Series"                      
[209] "2022 FAST5 - World Netball Series Finals"               
[210] "2023 Super Netball"                                     
[211] "2023 Super Netball Finals"                              
[212] "Fast5 Mens 2022"                                        
[213] "Fast5 Mens Final 2022"                                  
[214] "Diamond Challenge 2022"                                 
[215] "Netball South Africa President XII"                     
[216] "2023 Telstra NRL Premiership"                           
[217] "2023 Telstra NRL Finals"                                
[218] "2023 January Netball Quad Series"                       
[219] "2023 January Netball Quad Series Finals"                
[220] "NWC Preliminaries Stage 1 and 2"                        
[221] "NWC Play-offs and Placings"                             
[222] "Quad Supersport testing"                                
[223] "2023 National Netball League"                           
[224] "2023 National Netball League"                           
[225] "2023 All Stars"                                         
[226] "2023 State of Origin"                                   
[227] "2023 #TeamGirls Cup A"                                  
[228] "2023 #TeamGirls Cup B"                                  
[229] "2023 #TeamGirls Cup Finals"                             
[230] "2023 NRLW"                                              
[231] "2023 NRLW Finals"                                       
[232] "2023 State of Origin Womens"                            
[233] "2023 Taini Jamison Trophy"                              
[234] "NWC 2023 Testing"                                       
[235] "2023 Australian Netball Championships"                  
[236] "2023 Australian Netball Championships Finals"           
[237] "2023 Netball NZ Open Champs"                            
[238] "2023 Australia Men's v NZ"                              
[239] "2023 Constellation Cup"                                 
[240] "2023 Australia v South Africa"                          
[241] "2023 Pacific Championships - Mens"                      
[242] "2023 Pacific Championships - Womens"                    
[243] "2023 NZ Secondary Schools Champs"                       
[244] "2023 NZ Secondary Schools Champs Finals"                
[245] "2023 FAST5 World Netball Series"                        
[246] "2023 FAST5 World Netball Series Finals"                 
[247] "Fast5 Mens 2023"                                        
[248] "Fast5 Mens Final 2023"                                  
[249] "2024 All Stars"                                         
[250] "2024 Telstra NRL Premiership"                           
[251] "2024 Telstra NRL Finals"                                
[252] "2024 State of Origin"                                   
[253] "2024 Women's All Stars"                                 
[254] "2024 NRLW"                                              
[255] "2024 NRLW Finals"                                       
[256] "2024 State of Origin Womens"                            
[257] "2024 Netball Nations Cup"                               
[258] "2024 Netball Nations Cup Finals"                        
[259] "2024 Super Netball"                                     
[260] "2024 Super Netball Finals"                              
[261] "2024 #TeamGirls Cup A"                                  
[262] "2024 #TeamGirls Cup B"                                  
[263] "2024 #TeamGirls Cup Finals"                             
[264] "2024 ANZ Premiership"                                   
[265] "2024 ANZ Premiership Finals"                            
[266] "2024 National Netball League"                           
[267] "2024 National Netball League"                           
[268] "Pac. Netball Series"                                    
[269] "PNS - Place/Final"                                      
[270] "2024 Constellation Cup"                                 
[271] "2024 Taini Jamison Trophy"                              
[272] "2024 England Series"                                    
[273] "2024 Mens Aus v NZ"                                     
[274] "2024 FAST5 World Netball Series"                        
[275] "2024 FAST5 World Netball Series Finals"                 
[276] "Fast5 Mens 2024"                                        
[277] "Fast5 Mens Final 2024"                                  
[278] "Pacific Cup Men 2024"                                   
[279] "Pacific Bowl Men 2024"                                  
[280] "Pacific Cup Women 2024"                                 
[281] "Pacific Bowl Women 2024"                                
[282] "2025 Super Netball"                                     
[283] "2025 Super Netball Finals"                              
[284] "2025 Telstra NRL Premiership"                           
[285] "2025 NRLW"                                              
[286] "2025 All Stars"                                         
[287] "2025 Womens's All Stars"                                
[288] "2025 State of Origin"                                   
[289] "2025 State of Origin Womens"                            
[290] "2025 #TeamGirls Cup A"                                  
[291] "2025 #TeamGirls Cup B"                                  
[292] "2025 #TeamGirls Cup Finals"                             
[293] "Pac. Netball Series"                                    
[294] "PNS - Place/Final"                                      
[295] "2025 ANZ Premiership"                                   
[296] "2025 ANZ Premiership Finals"                            
[297] "2025 National Netball League"                           
[298] "2025 National Netball League Finals"                    
[299] "2025 Taini Jamison Trophy"                              
[300] "2025 Australia v South Africa"                          
[301] "2025 Constellation Cup"                                 
[302] "2025 Constellation Cup Series Decider"                  
[303] "2026 Super Netball"                                     
[304] "2026 Super Netball Finals"                              
[305] "2025 Telstra NRL Finals"                                
[306] "2025 NRLW Finals"                                       
[307] "2025 Scotland v New Zealand"                            
[308] "2025 England v New Zealand"                             
[309] "2026 Telstra NRL Premiership"                           
[310] "2026 State of Origin"                                   
[311] "2026 Australia v Jamaica"                               
[312] "2026 ANZ Premiership"                                   
[313] "2026 ANZ Premiership Finals"                            
[314] "2026 NRLW"                                              
[315] "2026 NRLW Finals"                                       
[316] "2026 State of Origin Womens"                            

2a. fetch_player_stats(source='championdata')

--- SCHEMA: championdata player logs ---
  272 rows x 63 cols
    tries                      integer    e.g. 3
    runsDummyHalfMetres        integer    e.g. 0
    sinBins                    integer    e.g. 0
    onReports                  integer    e.g. 0
    runsHitupMetres            integer    e.g. 0
    tryAssists                 integer    e.g. 0
    penaltyGoalAttempts        integer    e.g. 0
    points                     integer    e.g. 12
    conversionsUnsuccessful    integer    e.g. 0
    possessions                integer    e.g. 33
    tackleds                   integer    e.g. 19
    kickMetres                 integer    e.g. 0
    kicksGeneralPlay           integer    e.g. 0
    tackles                    integer    e.g. 4
    tacklesIneffective         integer    e.g. 0
    handlingErrors             integer    e.g. 0
    sentOffs                   integer    e.g. 0
    runsDummyHalf              integer    e.g. 0
    squadId                    integer    e.g. 335
    offloads                   integer    e.g. 0
    bombKicksCaught            integer    e.g. 1
    runsKickReturn             integer    e.g. 4
    runsHitup                  integer    e.g. 0
    fieldGoalAttempts          integer    e.g. 0
    conversionAttempts         integer    e.g. 0
    penaltiesConceded          integer    e.g. 0
    postContactMetres          integer    e.g. 0
    position                   character  e.g. -
    errors                     integer    e.g. 2
    goalLineDropouts           integer    e.g. 0
    fortyTwenty                integer    e.g. 0
    conversions                integer    e.g. 0
    tryDebits                  integer    e.g. 0
    missedTackles              integer    e.g. 0
    penaltyGoalsUnsuccessful   integer    e.g. 0
    kicksCaught                integer    e.g. 1
    metresGained               integer    e.g. 187
    lineBreaks                 integer    e.g. 3
    tackleBreaks               integer    e.g. 7
    trySaves                   integer    e.g. 0
    passes                     integer    e.g. 9
    runMetres                  integer    e.g. 187
    fieldGoalsUnsuccessful     integer    e.g. 0
    lineBreakAssists           integer    e.g. 1
    runsNormalMetres           integer    e.g. 187
    runsNormal                 integer    e.g. 18
    runsKickReturnMetres       integer    e.g. 0
    playerId                   integer    e.g. 31036
    jumperNumber               integer    e.g. 1
    fieldGoals                 integer    e.g. 0
    penaltyGoals               integer    e.g. 0
    runs                       integer    e.g. 22
    firstname                  character  e.g. Greg
    surname                    character  e.g. Inglis
    shortDisplayName           character  e.g. Inglis, G
    displayName                character  e.g. G.Inglis
    match_id                   integer    e.g. 91350101
    competition_id             integer    e.g. 9135
    round                      integer    e.g. 1
    team_name                  character  e.g. South Sydney Rabbitohs
    team_location              character  e.g. home
    match_status               character  e.g. complete
    utc_start                  character  e.g. 2014-03-06T09:05:01Z

2b. fetch_player_stats(source='rugbyproject')
Found 18 valid matches for nrl 2026

--- SCHEMA: rugbyproject player logs ---
  (empty / NULL)

3. writing sample JSON (whichever source returned rows)
  wrote nrl_player_logs_sample.json from 'championdata' (272 rows)

DONE. Paste sections 0, 1, 2a, 2b back.
Section 2a vs 2b decides the log source; the column lists decide the signal/DVP build.
