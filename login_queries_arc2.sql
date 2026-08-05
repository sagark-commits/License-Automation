-- TMONE login count queries - ARC-2
-- Database: oneproduct @ 10.28.9.110
-- Peak date: replace :PEAK_DATE with the peak day from the utilization workbook

-- ========== MBSA_ARCH2 ==========
-- Tenant: MBSA_ARCH2 | Sheet: MBSA_Ameyo-Express_ARCH2 | contact_center_id=51 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 51::text) AND c.contact_center_id = 51 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== NSRC ==========
-- Tenant: NSRC | Sheet: NSRC Ameyo-Express | contact_center_id=53 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 53::text) AND c.contact_center_id = 53 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: NSRC | Sheet: NSRC Supervisor | contact_center_id=53 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 53::text) AND c.contact_center_id = 53 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: NSRC | Sheet: NSRC WallBoard | contact_center_id=53 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.contact_center_id = 53 AND u.login_time::date = ':PEAK_DATE' AND (d.user_type ILIKE '%%Wallboard%%' OR d.user_id ILIKE '%%wallboard%%') ORDER BY u.login_time;

-- ========== PETRON_ARCH2 ==========
-- Tenant: PETRON_ARCH2 | Sheet: Ameyo-Express_Petron_ARCH2 | contact_center_id=48 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 48::text) AND c.contact_center_id = 48 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PETRON_ARCH2 | Sheet: Supervisor_Petron_ARCH2 | contact_center_id=48 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 48::text) AND c.contact_center_id = 48 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PETRON_ARCH2 | Sheet: Wallboard_Petron_ARCH2 | contact_center_id=48 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.contact_center_id = 48 AND u.login_time::date = ':PEAK_DATE' AND (d.user_type ILIKE '%%Wallboard%%' OR d.user_id ILIKE '%%wallboard%%') ORDER BY u.login_time;

-- ========== PMCARE ==========
-- Tenant: PMCARE | Sheet: Ameyo-Express_PMCARE | contact_center_id=20 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 20::text) AND c.contact_center_id = 20 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: PMCARE | Sheet: Supervisor_PMCARE | contact_center_id=20 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 20::text) AND c.contact_center_id = 20 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== TALENT ==========
-- Tenant: TALENT | Sheet: Talent crop Ameyo-Express agent | contact_center_id=47 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 47::text) AND c.contact_center_id = 47 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: TALENT | Sheet: Talent crop Supervisor | contact_center_id=47 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id IN (SELECT id FROM campaign_context WHERE contact_center_id = 47::text) AND c.contact_center_id = 47 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;

-- ========== TESCO ==========
-- Tenant: TESCO | Sheet: Ameyo-Express_TESCO | contact_center_id=5 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id WHERE d.contact_center_id = 5 AND u.login_time::date = ':PEAK_DATE' AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) ORDER BY u.login_time;
-- Tenant: TESCO | Sheet: Supervisor_TESCO | contact_center_id=5 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id WHERE d.contact_center_id = 5 AND u.login_time::date = ':PEAK_DATE' AND d.user_type = ANY(ARRAY['Supervisor']) ORDER BY u.login_time;
-- Tenant: TESCO | Sheet: Wallboard_TESCO | contact_center_id=5 | license=wallboard
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id WHERE d.contact_center_id = 5 AND u.login_time::date = ':PEAK_DATE' AND d.user_id ILIKE '%%wallboard%%' ORDER BY u.login_time;

-- ========== TMSRC_ARCH2 ==========
-- Tenant: TMSRC_ARCH2 | Sheet: Ameyo-express-TMSRC-ARCH-2 | contact_center_id=16 | license=agent
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['98', '99', '100', '101', '102', '103']) AND c.contact_center_id = 16 AND d.user_type = ANY(ARRAY['Professional-Agent', 'Ameyo-express']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
-- Tenant: TMSRC_ARCH2 | Sheet: Sup_TMRSC-ARCH-2 | contact_center_id=16 | license=supervisor
-- Replace :PEAK_DATE with peak date from utilization report
SELECT u.user_id, u.login_time, u.logout_time, (u.logout_time - u.login_time) AS duration FROM user_session_history u JOIN users d ON u.user_id = d.user_id JOIN campaign_user_working_history c ON u.session_id = c.session_id WHERE c.campaign_id::text = ANY(ARRAY['98', '99', '100', '101', '102', '103']) AND c.contact_center_id = 16 AND d.user_type = ANY(ARRAY['Supervisor']) AND u.login_time::date = ':PEAK_DATE' ORDER BY u.login_time;
