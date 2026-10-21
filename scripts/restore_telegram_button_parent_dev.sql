-- PostgreSQL, только соединение DEV. Автоматически не запускать.
-- По умолчанию это предпросмотр с ROLLBACK.
-- После проверки результата для применения вручную заменить ROLLBACK на COMMIT.
BEGIN;

DO $$
DECLARE
    changed_count INTEGER;
BEGIN
    UPDATE bot_button_settings
    SET parent_path = ''
    WHERE id = 'aa7937c5-8a8c-4fb8-8ae5-b4eb273f6a71'
      AND menu_type = 'Колл Центр'
      AND parent_path = '❌Не берет'
      AND workflow_type = 'auto'
      AND automation_key = 'telegram_group'
      AND telegram_group_id IS NOT NULL
      AND deleted_at IS NULL;

    GET DIAGNOSTICS changed_count = ROW_COUNT;
    IF changed_count <> 1 THEN
        RAISE EXCEPTION 'Ожидалась одна запись с указанными условиями; изменено: %', changed_count;
    END IF;
END;
$$;

SELECT id, button_key, stable_button_id, parent_path, workflow_type, automation_key, telegram_group_id
FROM bot_button_settings
WHERE id = 'aa7937c5-8a8c-4fb8-8ae5-b4eb273f6a71';

ROLLBACK;
