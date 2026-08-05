-- TMONE login count queries - ARC-1
-- Database: oneproduct @ 10.28.9.103
-- Peak date: replace :PEAK_DATE with the peak day from the utilization workbook

-- ========== AIG_FM ==========
-- Tenant: AIG_FM | Sheet: Ameyo-express_AIG_FM | contact_center_id=14 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 14::text) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: AIG_FM | Sheet: ARCH-1_Supervisor_AIG_FM | contact_center_id=14 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 14::text) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== AIG_FMAD_132 ==========
-- Tenant: AIG_FMAD_132 | Sheet: 132 _ Ameyo-Pro-Dialer | contact_center_id=14 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['132']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: AIG_FMAD_132 | Sheet: 132 _sup  | contact_center_id=14 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['132']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== BONUSLINK_209 ==========
-- Tenant: BONUSLINK_209 | Sheet: Bonuslink_209_Ameyo-Express | contact_center_id=14 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['209']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: BONUSLINK_209 | Sheet: Bonuslink_209_Sup | contact_center_id=14 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['209']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== BONUSLINK_226 ==========
-- Tenant: BONUSLINK_226 | Sheet: Bonuslink_226_Ameyo-Express | contact_center_id=14 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['226']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: BONUSLINK_226 | Sheet: Bonuslink_226 _ Supervispor  | contact_center_id=14 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['226']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== CCLITE ==========
-- Tenant: CCLITE | Sheet: Ameyo-Express_CCLite | contact_center_id=17 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 17::text) AND c.contact_center_id = 17 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: CCLITE | Sheet: Supervisor_CCLite | contact_center_id=17 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 17::text) AND c.contact_center_id = 17 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== GAMUDA ==========
-- Tenant: GAMUDA | Sheet: Ameyo-Express_Gamuda | contact_center_id=45 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 45::text) AND c.contact_center_id = 45 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== IGLOO_247 ==========
-- Tenant: IGLOO_247 | Sheet: igloo_247_Ameyo-Express | contact_center_id=14 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['247']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: IGLOO_247 | Sheet: Igloo247_Sup | contact_center_id=14 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['247']) AND c.contact_center_id = 14 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== ISADA ==========
-- Tenant: ISADA | Sheet: Ameyo-Express_iSADA | contact_center_id=35 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 35::text) AND c.contact_center_id = 35 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: ISADA | Sheet: Supervisor_iSADA | contact_center_id=35 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 35::text) AND c.contact_center_id = 35 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: ISADA | Sheet: Wallboard_user_iSADA | contact_center_id=35 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.contact_center_id = 35 AND u.login_time::date = ':PEAK_DATE' AND (d.user_type ILIKE '%%Wallboard%%' OR d.user_id ILIKE '%%wallboard%%') ORDER BY u.login_time;

-- ========== KPWKM ==========
-- Tenant: KPWKM | Sheet: KPMW_Ameyo-Express | contact_center_id=43 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 43::text) AND c.contact_center_id = 43 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== MBSA ==========
-- Tenant: MBSA | Sheet: MBSA Ameyo-express | contact_center_id=53 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['268']) AND c.contact_center_id = 53 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== MBSP ==========
-- Tenant: MBSP | Sheet: Ameyo-Express-MBSP-ARC-1 | contact_center_id=7 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['69']) AND c.contact_center_id = 7 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: MBSP | Sheet: MBSP-Supervisor | contact_center_id=7 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['69']) AND c.contact_center_id = 7 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: MBSP | Sheet: MBSP-WALLBOARD | contact_center_id=7 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u WHERE u.user_id = 'wallboard_mbsp' AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== OCC_ZEN ==========
-- Tenant: OCC_ZEN | Sheet: Ameyo-Express_OCN | contact_center_id=29 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 29::text) AND c.contact_center_id = 29 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: OCC_ZEN | Sheet: supervisor_OCN | contact_center_id=29 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 29::text) AND c.contact_center_id = 29 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== PBAPP ==========
-- Tenant: PBAPP | Sheet: Ameyo-Express-PBAPP | contact_center_id=7 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['150', '151', '130', '70', '182']) AND c.contact_center_id = 7 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PBAPP | Sheet: SUPERVISOR-PBAPP-ARC-1 | contact_center_id=7 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['150', '151', '130', '70', '182']) AND c.contact_center_id = 7 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== PETRON_ARCH1 ==========
-- Tenant: PETRON_ARCH1 | Sheet: ARCH-1_Petron Ameyo-Express | contact_center_id=48 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['269', '264', '251', '275']) AND c.contact_center_id = 48 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PETRON_ARCH1 | Sheet: Arch1 Petron_Supervisor | contact_center_id=48 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['269', '264', '251', '275']) AND c.contact_center_id = 48 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PETRON_ARCH1 | Sheet: Arch_1_Petron_Wallboard | contact_center_id=48 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.contact_center_id = 48 AND u.login_time::date = ':PEAK_DATE' AND (d.user_type ILIKE '%%Wallboard%%' OR d.user_id ILIKE '%%wallboard%%') ORDER BY u.login_time;

-- ========== PRUBSN ==========
-- Tenant: PRUBSN | Sheet: Pro-Dialer_PruBSN | contact_center_id=27 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 27::text) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PRUBSN | Sheet: Supervisor_PruBSN | contact_center_id=27 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 27::text) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== PRUBSN_168 ==========
-- Tenant: PRUBSN_168 | Sheet: 168_PrUBSN-Ameyo-Express | contact_center_id=27 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['168']) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PRUBSN_168 | Sheet: 168_sup | contact_center_id=27 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['168']) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== PRUBSN_169 ==========
-- Tenant: PRUBSN_169 | Sheet: 169_Ameyo-Express | contact_center_id=27 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['169']) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PRUBSN_169 | Sheet: 169_Sup | contact_center_id=27 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['169']) AND c.contact_center_id = 27 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== SAMB ==========
-- Tenant: SAMB | Sheet: Ameyo-Express_SAMB | contact_center_id=10 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 10::text) AND c.contact_center_id = 10 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: SAMB | Sheet: Supervisor_SAMB | contact_center_id=10 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 10::text) AND c.contact_center_id = 10 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: SAMB | Sheet: Excutive_SAMB | contact_center_id=10 | license=executive
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 10::text) AND c.contact_center_id = 10 AND d.user_type = ANY(ARRAY['Executive']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== SARAWAK ==========
-- Tenant: SARAWAK | Sheet: Ameyo-Express_Sarawak | contact_center_id=52 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 52::text) AND c.contact_center_id = 52 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== SATU ==========
-- Tenant: SATU | Sheet: Ameyo-Express_SATU | contact_center_id=26 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 26::text) AND c.contact_center_id = 26 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: SATU | Sheet: Supervisor_SATU | contact_center_id=26 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 26::text) AND c.contact_center_id = 26 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== TABUNG ==========
-- Tenant: TABUNG | Sheet: Ameyo-Express_TABUNGHAJI | contact_center_id=18 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 18::text) AND c.contact_center_id = 18 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: TABUNG | Sheet: Supervisor_TABUNGHAJI | contact_center_id=18 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 18::text) AND c.contact_center_id = 18 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== TMSRC_ARCH1 ==========
-- Tenant: TMSRC_ARCH1 | Sheet: Ameyo-Express_TMSRC (ARCH1 | contact_center_id=16 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['98', '99', '100', '101', '102', '103']) AND c.contact_center_id = 16 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: TMSRC_ARCH1 | Sheet: ARCH-1-Superviosr_TMSRC | contact_center_id=16 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['98', '99', '100', '101', '102', '103']) AND c.contact_center_id = 16 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== UNI5G ==========
-- Tenant: UNI5G | Sheet: Ameyo-Express_UNI5G | contact_center_id=23 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 23::text) AND c.contact_center_id = 23 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
