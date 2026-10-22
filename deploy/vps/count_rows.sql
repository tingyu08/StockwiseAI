-- 每張表的精確筆數＋目前 migration 版本，搬遷前後各跑一次再 diff。
-- 需經 psql 的 stdin 執行（\gexec 是 psql 指令），用法見 MIGRATION.md。
SELECT 'alembic_version=' || version_num FROM alembic_version;

SELECT format(
    'SELECT %L || ''='' || count(*) FROM public.%I',
    tablename, tablename
)
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename
\gexec
