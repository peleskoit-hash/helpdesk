from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import html
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AutomaticMessageTemplate, TelegramContact, Ticket, User
from .telegram_text_format import TelegramHtmlValidationError, prepare_telegram_html_message, validate_telegram_html
from .template_fields import template_context

_FIELD_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
_EMPTY = "не задано"


@dataclass(frozen=True)
class AutomaticMessageDefinition:
    key: str
    title: str
    description: str
    category: str
    category_title: str
    sort_order: int
    default_text: str
    audience: str = "client"
    channel: str = "telegram"
    html_enabled: bool = True
    allowed_variables: tuple[str, ...] = ()
    variable_scopes: tuple[str, ...] = ()
    explicit_variables: tuple[str, ...] = ()
    runtime_enabled: bool = False


@dataclass(frozen=True)
class NotificationBotBasicTag:
    semantic: str
    admin_label: str
    personal_label: str
    admin_description: str
    personal_description: str

    def label(self, context: str) -> str:
        return self.personal_label if context == "personal" else self.admin_label

    def description(self, context: str) -> str:
        return self.personal_description if context == "personal" else self.admin_description


AUTOMATIC_MESSAGE_REGISTRY: tuple[AutomaticMessageDefinition, ...] = (
    AutomaticMessageDefinition(
        key="telegram.onboarding.first_connection.client",
        title="Первое подключение клиента",
        description="Приветствие и объяснение первичного обращения при первом /start",
        category="start_menu",
        category_title="Старт и меню",
        sort_order=10,
        default_text=(
            "Здравствуйте, Вы подключаетесь к нашему боту тех. поддержки в первый раз.\n"
            "Сейчас Ваша заявка будет распределена на свободного (освободившегося) сотрудника.\n"
            "Будьте готовы сообщить Ваши Фамилию и Имя, а так же отдел к которому Вы относитесь.\n"
            "Если Вы проходите стажировку, сразу сообщите об этом."
        ),
        variable_scopes=("client", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.intake.menu_timeout",
        title="Таймаут выбора меню",
        description="Создание заявки прерывается, если клиент не выбрал конечный пункт меню",
        category="start_menu",
        category_title="Старт и меню",
        sort_order=20,
        default_text=(
            "Вы не выбрали нужное меню, создание заявки прервано.\n"
            "Для создания новой нажмите\n/start."
        ),
        variable_scopes=("client", "ticket", "timeout", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.menu.problem_not_selected",
        title="Сообщение без выбранной конечной кнопки",
        description="Клиент пишет текст, но ещё не выбрал конечную кнопку меню",
        category="ticket_creation",
        category_title="Создание заявки",
        sort_order=10,
        default_text=(
            "Сначала выберите конечную проблему в меню. Пока выбор не сделан, сообщение не дойдёт до технической поддержки.\n"
            "Если у Вас не открыто меню нажмите\n/start"
        ),
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.ticket.create.default_client_text",
        title="Заявка создана",
        description="Запасной текст после создания заявки, если у кнопки нет своего текста клиенту",
        category="ticket_creation",
        category_title="Создание заявки",
        sort_order=20,
        default_text="Заявка создана. Опишите проблему сообщением.",
        variable_scopes=("client", "ticket", "button"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.task.request_text",
        title="Запрос описания задачи",
        description="Просьба написать текст после выбора task/group_task кнопки",
        category="tasks",
        category_title="Задачи",
        sort_order=10,
        default_text=(
            "Опишите задачу одним сообщением.\n"
            "Если текст не будет написан в течение {{timeout_minutes}} мин., создание задачи будет отменено."
        ),
        variable_scopes=("client", "ticket", "timeout", "button"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.task.created",
        title="Задача создана",
        description="Клиент получает текст после того, как задача создана и передана в работу",
        category="tasks",
        category_title="Задачи",
        sort_order=20,
        default_text="Заявка создана и передана в работу.\n\nДля создания другой нажмите\n /start",
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.intake.task_timeout",
        title="Таймаут описания задачи",
        description="Создание задачи прерывается, если клиент не написал текст задачи",
        category="tasks",
        category_title="Задачи",
        sort_order=30,
        default_text=(
            "Создание заявки было прервано т.к. текст не был написан в течении {{timeout_minutes}} мин.\n\n"
            "Для создания новой нажмите\n/start"
        ),
        variable_scopes=("client", "ticket", "timeout", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.task.completion.with_answer",
        title="Задача завершена с ответом",
        description="Клиентский текст при закрытии обычной задачи с текстовым ответом сотрудника",
        category="tasks",
        category_title="Задачи",
        sort_order=40,
        default_text=(
            "По Вашей задаче \"{{ticket_subject}}\" от {{ticket_created_at}}\n"
            "\"{{client_message}}\"\n\n"
            "Получен ответ:\n"
            "{{agent_answer}}"
        ),
        variable_scopes=("client", "ticket"),
        explicit_variables=("client_message", "agent_answer"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.task.completion.files_only",
        title="Задача завершена с файлами",
        description="Клиентский текст при закрытии обычной задачи без текстового ответа, но с файлами сотрудника",
        category="tasks",
        category_title="Задачи",
        sort_order=50,
        default_text=(
            "По Вашей задаче \"{{ticket_subject}}\" от {{ticket_created_at}}\n"
            "\"{{client_message}}\"\n\n"
            "Получены файлы от сотрудника:"
        ),
        variable_scopes=("client", "ticket"),
        explicit_variables=("client_message",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.task.closed_without_answer.client",
        title="Задача закрыта без ответа",
        description="Клиентский текст при удалении или принудительном закрытии обычной задачи без ответа",
        category="tasks",
        category_title="Задачи",
        sort_order=60,
        default_text=(
            "Ваша задача:\n"
            "\"{{ticket_subject}}\" от {{ticket_created_at}}\n"
            "с текстом:\n"
            "\"{{client_message}}\"\n"
            "закрыта без ответа по причине некорректных или неполных данных.\n\n"
            "Для создания новой нажмите\n"
            "{{start_command}}"
        ),
        variable_scopes=("client", "ticket", "menu"),
        explicit_variables=("client_message",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.group_task.completion.with_answers",
        title="Групповая задача завершена с ответами",
        description="Клиентский текст при завершении group_task parent, когда по child-задачам есть текстовые ответы",
        category="group_tasks",
        category_title="Групповые задачи",
        sort_order=70,
        default_text=(
            'По Вашей задаче "{{ticket_subject}}" от {{ticket_created_at}}\n'
            '"{{client_message}}"\n\n'
            'Получены ответы:\n'
            '{{group_task_answers}}\n\n'
            'Для создания новой нажмите\n'
            '{{start_command}}'
        ),
        variable_scopes=("client", "ticket", "menu"),
        explicit_variables=("client_message", "group_task_answers"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.group_task.completion.files_only",
        title="Групповая задача завершена файлами",
        description="Клиентский текст при завершении group_task parent, когда текстовых ответов нет",
        category="group_tasks",
        category_title="Групповые задачи",
        sort_order=80,
        default_text=(
            'По Вашей задаче "{{ticket_subject}}" от {{ticket_created_at}}\n'
            '"{{client_message}}"\n\n'
            'Получены файлы от сотрудников.\n\n'
            'Для создания новой нажмите\n'
            '{{start_command}}'
        ),
        variable_scopes=("client", "ticket", "menu"),
        explicit_variables=("client_message",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.wrong_problem.hint.client",
        title="Подсказка по неверной проблеме",
        description="Клиентский текст с подсказкой, какой пункт меню выбрать в следующий раз",
        category="wrong_problem",
        category_title="Неверная проблема",
        sort_order=10,
        default_text=(
            "Вы выбрали неподходящий раздел меню для решения этой проблемы.\n\n"
            "В следующий раз для в такой же ситуации выберите:\n"
            "«{{correct_problem_label}}»\n\n\n"
            "Это поможет быстрее и точнее решить новую задачу."
        ),
        variable_scopes=("client", "ticket"),
        explicit_variables=("correct_problem_label",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.ticket.close.client",
        title="Обращение закрыто",
        description=(
            "Клиентский текст при обычном закрытии обращения клиентом или сотрудником. "
            "При закрытии обычной заявки к этому сообщению автоматически добавляется системная кнопка "
            "«Оставить отзыв», если отзыв может быть привязан к сотруднику. Редактируется только текст сообщения."
        ),
        category="close_restore",
        category_title="Закрытие и возврат",
        sort_order=10,
        default_text="Ваше обращение закрыто. Если Вам снова будет необходима помощь технической поддержки, нажмите /start.",
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.auto_close.client",
        title="Автоматическое закрытие",
        description="Сообщение при автоматическом закрытии из-за отсутствия активности",
        category="close_restore",
        category_title="Закрытие и возврат",
        sort_order=20,
        default_text=(
            "Ваша заявка закрыта в связи с отсутствием активности в ней, "
            "если Ваша проблема не была решена, пожалуйста, создайте новую нажав /start"
        ),
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.ticket.restore.client",
        title="Заявка возвращена в работу",
        description="Клиентский текст при возврате закрытой заявки в работу",
        category="close_restore",
        category_title="Закрытие и возврат",
        sort_order=30,
        default_text="Вашу заявку «{{ticket_subject}}» от {{ticket_created_at}} вернули в работу.",
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.ticket.close.no_active.client",
        title="Нет активного обращения",
        description="Клиент нажал /close, но активное обращение не найдено",
        category="close_restore",
        category_title="Закрытие и возврат",
        sort_order=40,
        default_text="Активное обращение не найдено. Для создания новой заявки нажмите {{start_command}}.",
        variable_scopes=("client", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.review.rating_prompt",
        title="Выбор оценки",
        description="Клиентский текст на первом экране сценария отзыва",
        category="reviews",
        category_title="Отзывы",
        sort_order=10,
        default_text="Оцените, пожалуйста, качество работы сотрудника.",
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.review.text_prompt",
        title="Запрос текста отзыва",
        description="Клиентский текст с просьбой оставить комментарий к оценке",
        category="reviews",
        category_title="Отзывы",
        sort_order=20,
        default_text="Если хотите, напишите комментарий к вашей оценке.",
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.review.attachment_no_text",
        title="Вложение без текста",
        description="Клиентский текст для вложения, которое не может быть сохранено как отзыв без текста",
        category="reviews",
        category_title="Отзывы",
        sort_order=30,
        default_text="Вложение не будет добавлено к отзыву. Отправьте текст сообщения или добавьте подпись к вложению.",
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.review.attachment_caption",
        title="Подпись к вложению сохранена",
        description="Клиентский текст после сохранения подписи вложения как текста отзыва",
        category="reviews",
        category_title="Отзывы",
        sort_order=40,
        default_text="Подпись к вложению сохранена как текст отзыва. Вы можете отправить другой текст или завершить отзыв.",
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.review.thank_you",
        title="Благодарность за отзыв",
        description="Клиентский текст благодарности после завершения сценария отзыва",
        category="reviews",
        category_title="Отзывы",
        sort_order=50,
        default_text="Спасибо за ваш отзыв!",
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.attachment.unsupported_type",
        title="Неподдерживаемый тип вложения",
        description="Клиент отправил voice/video или другой тип, который бот не доставляет сотруднику",
        category="attachments",
        category_title="Файлы и вложения",
        sort_order=10,
        default_text=(
            "Голосовые и видеосообщения технически не поддерживаются в данном сервисе и не будут доставлены сотруднику тех.поддержки.\n"
            "Пожалуйста, опишите проблему текстом и/или прикрепите файл/скриншот."
        ),
        variable_scopes=("client", "ticket"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.attachment.too_large",
        title="Слишком большой файл",
        description="Предупреждение клиента о файле больше лимита Telegram Bot API",
        category="attachments",
        category_title="Файлы и вложения",
        sort_order=20,
        default_text="Файл «{{file_name}}» имеет размер {{file_size}} КБ и не может быть отправлен.",
        variable_scopes=("client", "ticket", "attachment"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.start.active_ticket.client",
        title="Уже есть активное обращение",
        description="Клиент нажал /start, когда у него уже есть активная заявка",
        category="start_menu",
        category_title="Старт и меню",
        sort_order=30,
        default_text="У Вас уже есть активное обращение. Напишите сообщение, и оно добавится в текущую заявку.",
        variable_scopes=("client", "ticket", "menu"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.automation.fallback_created",
        title="Автоматизация создала обращение",
        description="Запасное сообщение, когда автоматизация не смогла выполнить действие сама",
        category="automation",
        category_title="Автоматизация",
        sort_order=10,
        default_text="Заявка создана и передана в работу.",
        variable_scopes=("client", "ticket"),
        explicit_variables=("reason",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.automation.material_unavailable.client",
        title="Материал недоступен",
        description="Автоматическая отправка материала не удалась, обращение передано сотруднику",
        category="automation",
        category_title="Автоматизация",
        sort_order=20,
        default_text="Материал временно недоступен. Обращение передано сотруднику технической поддержки.",
        variable_scopes=("client", "ticket"),
        explicit_variables=("reason",),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.automation.bitrix_deal_format_error_client",
        title="Bitrix: сделка не распознана",
        description="Клиент отправил некорректный номер или ссылку сделки для автоматизации Bitrix",
        category="automation",
        category_title="Автоматизация",
        sort_order=30,
        default_text=(
            "Не удалось распознать сделку.\n\n"
            "Отправьте одним сообщением номер сделки из 5–7 цифр или ссылку вида:\n"
            "https://crm.elitesochi.com/crm/deal/show/2450346/"
        ),
        variable_scopes=("client", "ticket"),
        runtime_enabled=True,
    ),
    AutomaticMessageDefinition(
        key="telegram.assignment.agent_assigned",
        title="Назначен исполнитель",
        description="Сообщение клиенту о сотруднике, который назначен на заявку",
        category="assignment",
        category_title="Распределение и очередь",
        sort_order=10,
        default_text="Вам ответит <b>{{agent_full_name}}</b>",
        variable_scopes=("client", "ticket", "agent"),
        runtime_enabled=True,
    ),
)


NOTIFICATION_BOT_BASIC_CATEGORY = "notification_bot_basic"

NOTIFICATION_BOT_BASIC_DEFINITIONS: tuple[AutomaticMessageDefinition, ...] = (
    AutomaticMessageDefinition(
        key="telegram.notification_bot.basic.new_assignment",
        title="Новая заявка",
        description="Уведомление сотруднику о новой назначенной заявке",
        category=NOTIFICATION_BOT_BASIC_CATEGORY,
        category_title="Базовые уведомления в боте",
        sort_order=10,
        default_text=(
            "Вам назначена новая [Тип заявки]\n\n"
            "Автор: [Автор заявки/задачи]\n"
            "Подразделение: [Подразделение автора]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
        channel="employee_telegram",
        runtime_enabled=False,
    ),
    AutomaticMessageDefinition(
        key="telegram.notification_bot.basic.new_message",
        title="Новое сообщение",
        description="Уведомление сотруднику о новом сообщении в заявке",
        category=NOTIFICATION_BOT_BASIC_CATEGORY,
        category_title="Базовые уведомления в боте",
        sort_order=20,
        default_text=(
            "Новое сообщение в заявке\n\n"
            "Автор: [Автор сообщения]\n"
            "Вид: [Вид сообщения]\n\n"
            "[Тело сообщения]\n\n"
            "[Ссылка на заявку]"
        ),
        channel="employee_telegram",
        runtime_enabled=False,
    ),
    AutomaticMessageDefinition(
        key="telegram.notification_bot.basic.transfer_to_agent",
        title="Перевод на агента",
        description="Уведомление сотруднику о переданной ему заявке",
        category=NOTIFICATION_BOT_BASIC_CATEGORY,
        category_title="Базовые уведомления в боте",
        sort_order=30,
        default_text=(
            "Вам передана заявка\n\n"
            "Предыдущий агент: [Предыдущий агент]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
        channel="employee_telegram",
        runtime_enabled=False,
    ),
    AutomaticMessageDefinition(
        key="telegram.notification_bot.basic.transfer_from_agent",
        title="Перевод с агента",
        description="Уведомление сотруднику о заявке, переданной от него",
        category=NOTIFICATION_BOT_BASIC_CATEGORY,
        category_title="Базовые уведомления в боте",
        sort_order=40,
        default_text=(
            "Ваша заявка передана\n\n"
            "Куда передана: [Куда передана заявка]\n"
            "Администратор: [Администратор перевода]\n"
            "Заголовок: [Заголовок заявки]\n\n"
            "[Ссылка на заявку]"
        ),
        channel="employee_telegram",
        runtime_enabled=False,
    ),
)

_REGISTRY_BY_KEY = {item.key: item for item in AUTOMATIC_MESSAGE_REGISTRY}
_NOTIFICATION_BOT_BASIC_BY_TYPE = {
    definition.key.rsplit(".", 1)[-1]: definition
    for definition in NOTIFICATION_BOT_BASIC_DEFINITIONS
}

NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG = NotificationBotBasicTag(
    semantic="recipient_telegram_username",
    admin_label="[TG @username адресата]",
    personal_label="[Мой TG @username]",
    admin_description="Telegram @username сотрудника, которому отправляется уведомление.",
    personal_description="Ваш Telegram @username, на который приходит уведомление.",
)

NOTIFICATION_BOT_BASIC_TAG_REGISTRY: tuple[NotificationBotBasicTag, ...] = (
    NotificationBotBasicTag(
        semantic="ticket_number",
        admin_label="[Номер заявки]",
        personal_label="[Номер заявки]",
        admin_description="Номер заявки или задачи в ES DESK.",
        personal_description="Номер заявки или задачи в ES DESK.",
    ),
    NotificationBotBasicTag(
        semantic="ticket_type",
        admin_label="[Тип заявки]",
        personal_label="[Тип заявки]",
        admin_description="Тип: заявка, задача, системная задача и т. п.",
        personal_description="Тип: заявка, задача, системная задача и т. п.",
    ),
    NotificationBotBasicTag(
        semantic="ticket_subject",
        admin_label="[Заголовок заявки]",
        personal_label="[Заголовок заявки]",
        admin_description="Заголовок заявки или задачи.",
        personal_description="Заголовок заявки или задачи.",
    ),
    NotificationBotBasicTag(
        semantic="ticket_author",
        admin_label="[Автор заявки/задачи]",
        personal_label="[Автор заявки/задачи]",
        admin_description="Сотрудник или система, создавшие заявку или задачу.",
        personal_description="Сотрудник или система, создавшие заявку или задачу.",
    ),
    NotificationBotBasicTag(
        semantic="author_department",
        admin_label="[Подразделение автора]",
        personal_label="[Подразделение автора]",
        admin_description="Подразделение автора на момент создания.",
        personal_description="Подразделение автора на момент создания.",
    ),
    NotificationBotBasicTag(
        semantic="created_at",
        admin_label="[Дата создания]",
        personal_label="[Дата создания]",
        admin_description="Дата и время создания заявки или задачи.",
        personal_description="Дата и время создания заявки или задачи.",
    ),
    NotificationBotBasicTag(
        semantic="ticket_link",
        admin_label="[Ссылка на заявку]",
        personal_label="[Ссылка на заявку]",
        admin_description="Прямая ссылка на заявку или задачу в ES DESK.",
        personal_description="Прямая ссылка на заявку или задачу в ES DESK.",
    ),
    NotificationBotBasicTag(
        semantic="message_author",
        admin_label="[Автор сообщения]",
        personal_label="[Автор сообщения]",
        admin_description="Кто отправил новое сообщение или комментарий.",
        personal_description="Кто отправил новое сообщение или комментарий.",
    ),
    NotificationBotBasicTag(
        semantic="message_kind",
        admin_label="[Вид сообщения]",
        personal_label="[Вид сообщения]",
        admin_description="Тип сообщения: ответ или комментарий.",
        personal_description="Тип сообщения: ответ или комментарий.",
    ),
    NotificationBotBasicTag(
        semantic="message_body",
        admin_label="[Тело сообщения]",
        personal_label="[Тело сообщения]",
        admin_description="Текст сообщения и информация о прикреплённых файлах.",
        personal_description="Текст сообщения и информация о прикреплённых файлах.",
    ),
    NotificationBotBasicTag(
        semantic="message_created_at",
        admin_label="[Дата сообщения]",
        personal_label="[Дата сообщения]",
        admin_description="Дата и время отправки сообщения.",
        personal_description="Дата и время отправки сообщения.",
    ),
    NotificationBotBasicTag(
        semantic="previous_agent",
        admin_label="[Предыдущий агент]",
        personal_label="[Предыдущий агент]",
        admin_description="Сотрудник, от которого передали заявку.",
        personal_description="Сотрудник, от которого передали заявку.",
    ),
    NotificationBotBasicTag(
        semantic="transfer_admin",
        admin_label="[Администратор перевода]",
        personal_label="[Администратор перевода]",
        admin_description="Кто выполнил перевод заявки. Для автоматики — «Система».",
        personal_description="Кто выполнил перевод заявки. Для автоматики — «Система».",
    ),
    NotificationBotBasicTag(
        semantic="transfer_date",
        admin_label="[Дата перевода]",
        personal_label="[Дата перевода]",
        admin_description="Дата и время передачи заявки.",
        personal_description="Дата и время передачи заявки.",
    ),
    NotificationBotBasicTag(
        semantic="transfer_destination",
        admin_label="[Куда передана заявка]",
        personal_label="[Куда передана заявка]",
        admin_description="Новый сотрудник, «Распределение» или административный список.",
        personal_description="Новый сотрудник, «Распределение» или административный список.",
    ),
    NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG,
)

_NOTIFICATION_BOT_BASIC_TAG_BY_SEMANTIC = {
    tag.semantic: tag
    for tag in NOTIFICATION_BOT_BASIC_TAG_REGISTRY
}

_NOTIFICATION_BOT_BASIC_TAG_SEMANTICS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "new_assignment": (
        "ticket_number",
        "ticket_type",
        "ticket_subject",
        "ticket_author",
        "author_department",
        "created_at",
        "ticket_link",
        "recipient_telegram_username",
    ),
    "new_message": (
        "ticket_number",
        "ticket_type",
        "ticket_subject",
        "message_author",
        "message_kind",
        "message_body",
        "message_created_at",
        "ticket_link",
        "recipient_telegram_username",
    ),
    "transfer_to_agent": (
        "ticket_number",
        "ticket_type",
        "ticket_subject",
        "previous_agent",
        "transfer_admin",
        "transfer_date",
        "ticket_link",
        "recipient_telegram_username",
    ),
    "transfer_from_agent": (
        "ticket_number",
        "ticket_type",
        "ticket_subject",
        "transfer_destination",
        "transfer_admin",
        "transfer_date",
        "ticket_link",
        "recipient_telegram_username",
    ),
}

NOTIFICATION_BOT_BASIC_SEMANTIC_TAGS: tuple[NotificationBotBasicTag, ...] = (
    NOTIFICATION_BOT_RECIPIENT_TG_USERNAME_TAG,
)

NOTIFICATION_BOT_BASIC_TAGS: dict[str, tuple[str, ...]] = {
    "new_assignment": (
        "[Номер заявки]",
        "[Тип заявки]",
        "[Заголовок заявки]",
        "[Автор заявки/задачи]",
        "[Подразделение автора]",
        "[Дата создания]",
        "[Ссылка на заявку]",
    ),
    "new_message": (
        "[Номер заявки]",
        "[Тип заявки]",
        "[Заголовок заявки]",
        "[Автор сообщения]",
        "[Вид сообщения]",
        "[Тело сообщения]",
        "[Дата сообщения]",
        "[Ссылка на заявку]",
    ),
    "transfer_to_agent": (
        "[Номер заявки]",
        "[Тип заявки]",
        "[Заголовок заявки]",
        "[Предыдущий агент]",
        "[Администратор перевода]",
        "[Дата перевода]",
        "[Ссылка на заявку]",
    ),
    "transfer_from_agent": (
        "[Номер заявки]",
        "[Тип заявки]",
        "[Заголовок заявки]",
        "[Куда передана заявка]",
        "[Администратор перевода]",
        "[Дата перевода]",
        "[Ссылка на заявку]",
    ),
}


def notification_bot_basic_tags(notification_type: str, *, context: str = "admin") -> list[str]:
    tags = list(NOTIFICATION_BOT_BASIC_TAGS.get(notification_type, ()))
    tags.extend(tag.label(context) for tag in NOTIFICATION_BOT_BASIC_SEMANTIC_TAGS)
    return tags


def notification_bot_basic_tag_items(notification_type: str, *, context: str = "admin") -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for semantic in _NOTIFICATION_BOT_BASIC_TAG_SEMANTICS_BY_TYPE.get(notification_type, ()):
        tag = _NOTIFICATION_BOT_BASIC_TAG_BY_SEMANTIC.get(semantic)
        if tag is None:
            continue
        items.append(
            {
                "label": tag.label(context),
                "description": tag.description(context),
            }
        )
    return items


def notification_bot_basic_tag_labels_by_semantic(*, context: str = "admin") -> dict[str, str]:
    return {tag.semantic: tag.label(context) for tag in NOTIFICATION_BOT_BASIC_SEMANTIC_TAGS}


def normalize_notification_bot_basic_semantic_tag_labels(text: str | None, *, context: str = "admin") -> str:
    normalized = str(text or "")
    for tag in NOTIFICATION_BOT_BASIC_SEMANTIC_TAGS:
        target = tag.label(context)
        normalized = normalized.replace(tag.admin_label, target).replace(tag.personal_label, target)
    return normalized


def _format_recipient_tg_username(value: str | None) -> str:
    username = str(value or "").strip()
    if not username:
        return _EMPTY
    return username if username.startswith("@") else f"@{username}"


def render_notification_bot_basic_semantic_tags(
    text: str | None,
    *,
    recipient_telegram_username: str | None,
) -> str:
    rendered = str(text or "")
    values = {
        "recipient_telegram_username": _format_recipient_tg_username(recipient_telegram_username),
    }
    for tag in NOTIFICATION_BOT_BASIC_SEMANTIC_TAGS:
        value = values.get(tag.semantic, _EMPTY)
        rendered = rendered.replace(tag.admin_label, value).replace(tag.personal_label, value)
    return rendered

VARIABLE_DESCRIPTIONS: dict[str, str] = {
    "agent_answer": "Текст ответа сотрудника",
    "agent_full_name": "ФИО назначенного сотрудника",
    "agent_name": "Имя назначенного сотрудника",
    "button_label": "Название выбранной кнопки меню",
    "client_name": "Имя клиента",
    "correct_problem_label": "Правильный пункт меню, который нужно выбрать в следующий раз",
    "contact_first_name": "Имя клиента из карточки",
    "contact_full_name": "ФИО клиента из карточки",
    "contact_last_name": "Фамилия клиента из карточки",
    "department": "Отдел клиента",
    "destination": "Направление клиента из поля «Куда?»",
    "file_name": "Имя файла",
    "file_size": "Размер файла",
    "reason": "Причина или техническое описание события",
    "start_command": "Команда /start",
    "subject": "Название заявки или задачи",
    "telegram_display_name": "Имя клиента в Telegram",
    "telegram_chat_id": "Telegram chat id",
    "telegram_username": "Username клиента в Telegram",
    "telegram_user_id": "Telegram user id",
    "ticket_created_at": "Дата и время создания заявки",
    "ticket_id": "ID заявки",
    "ticket_status": "Статус заявки",
    "ticket_subject": "Название заявки или задачи",
    "timeout_minutes": "Время ожидания в минутах",
}

VARIABLE_SCOPES: dict[str, dict[str, object]] = {
    "client": {
        "title": "Клиент / Telegram",
        "variables": (
            "client_name",
            "contact_full_name",
            "contact_first_name",
            "contact_last_name",
            "destination",
            "department",
            "telegram_display_name",
            "telegram_username",
            "telegram_user_id",
            "telegram_chat_id",
        ),
    },
    "ticket": {
        "title": "Заявка",
        "variables": (
            "ticket_id",
            "ticket_subject",
            "subject",
            "ticket_status",
            "ticket_created_at",
        ),
    },
    "agent": {
        "title": "Сотрудник",
        "variables": (
            "agent_full_name",
            "agent_name",
        ),
    },
    "attachment": {
        "title": "Файлы",
        "variables": (
            "file_name",
            "file_size",
        ),
    },
    "timeout": {
        "title": "Таймаут",
        "variables": (
            "timeout_minutes",
        ),
    },
    "menu": {
        "title": "Меню / команда",
        "variables": (
            "start_command",
        ),
    },
    "button": {
        "title": "Меню / кнопка",
        "variables": (
            "button_label",
        ),
    },
}


def _automatic_message_variable_items(definition: AutomaticMessageDefinition) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_variable(name: str, scope: str, scope_title: str) -> None:
        variable_name = str(name or "").strip()
        if not variable_name or variable_name in seen:
            return
        seen.add(variable_name)
        result.append(
            {
                "name": variable_name,
                "description": VARIABLE_DESCRIPTIONS.get(variable_name, "Переменная шаблона"),
                "scope": scope,
                "scope_title": scope_title,
            }
        )

    for scope in definition.variable_scopes:
        scope_data = VARIABLE_SCOPES.get(scope)
        if not scope_data:
            continue
        scope_title = str(scope_data.get("title") or scope)
        for variable_name in scope_data.get("variables") or ():
            add_variable(str(variable_name), scope, scope_title)

    for variable_name in definition.allowed_variables:
        add_variable(variable_name, "explicit", "Дополнительно")
    for variable_name in definition.explicit_variables:
        add_variable(variable_name, "explicit", "Дополнительно")

    return result


def get_automatic_message_definition(key: str) -> AutomaticMessageDefinition | None:
    return _REGISTRY_BY_KEY.get(str(key or "").strip())


def list_automatic_message_definitions() -> list[AutomaticMessageDefinition]:
    return list(AUTOMATIC_MESSAGE_REGISTRY)


def get_notification_bot_basic_definition(notification_type: str) -> AutomaticMessageDefinition | None:
    return _NOTIFICATION_BOT_BASIC_BY_TYPE.get(str(notification_type or "").strip())


def list_notification_bot_basic_definitions() -> list[AutomaticMessageDefinition]:
    return list(NOTIFICATION_BOT_BASIC_DEFINITIONS)


def _normalize_body(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return normalized if normalized.strip() else None


def get_automatic_message_row(db: Session, key: str) -> AutomaticMessageTemplate | None:
    return db.execute(select(AutomaticMessageTemplate).where(AutomaticMessageTemplate.key == key)).scalar_one_or_none()


def get_effective_automatic_message_text(db: Session, key: str, fallback: str | None = None) -> str:
    definition = get_automatic_message_definition(key)
    default_text = fallback if fallback is not None else (definition.default_text if definition is not None else "")
    row = get_automatic_message_row(db, key)
    if row is not None and bool(row.is_overridden):
        body = _normalize_body(row.body)
        if body is not None:
            return body
    return default_text or ""


def _render_fields(text: str, context: dict[str, object], *, escape_values_for_html: bool = False) -> str:
    values = {
        key: html.escape(str(value), quote=False) if escape_values_for_html else str(value)
        for key, value in context.items()
        if value is not None
    }
    return _FIELD_RE.sub(lambda match: values.get(match.group(1), _EMPTY), text)


def render_automatic_message_text(
    db: Session,
    key: str,
    fallback: str | None = None,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
    extra: dict[str, object] | None = None,
    escape_values_for_html: bool = False,
) -> str:
    base = get_effective_automatic_message_text(db, key, fallback)
    context = template_context(db, ticket=ticket, contact=contact, agent=agent, client_message=client_message)
    context.update({
        "subject": context.get("ticket_subject", _EMPTY),
        "client_name": context.get("contact_full_name", _EMPTY),
        "agent_name": context.get("agent_full_name", _EMPTY),
        "start_command": "/start",
    })
    if extra:
        context.update(extra)
    return _render_fields(base, context, escape_values_for_html=escape_values_for_html)


def render_automatic_message_for_telegram(
    db: Session,
    key: str,
    fallback: str | None = None,
    *,
    ticket: Ticket | None = None,
    contact: TelegramContact | None = None,
    agent: User | None = None,
    client_message: str | None = None,
    extra: dict[str, object] | None = None,
) -> tuple[str, str | None]:
    text = render_automatic_message_text(
        db,
        key,
        fallback,
        ticket=ticket,
        contact=contact,
        agent=agent,
        client_message=client_message,
        extra=extra,
        escape_values_for_html=True,
    )
    return prepare_telegram_html_message(text)


def serialize_automatic_message(db: Session, definition: AutomaticMessageDefinition) -> dict:
    row = get_automatic_message_row(db, definition.key)
    body = _normalize_body(row.body if row is not None else None)
    is_overridden = bool(row and row.is_overridden and body is not None)
    variable_items = _automatic_message_variable_items(definition)
    return {
        **asdict(definition),
        "allowed_variables": [item["name"] for item in variable_items],
        "variable_scopes": list(definition.variable_scopes),
        "allowed_variable_items": variable_items,
        "default_text": definition.default_text,
        "override_text": body if row is not None else None,
        "effective_text": body if is_overridden else definition.default_text,
        "is_overridden": is_overridden,
        "html_enabled": bool(row.html_enabled) if row is not None and row.html_enabled is not None else definition.html_enabled,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


def serialize_notification_bot_basic_template(
    db: Session,
    definition: AutomaticMessageDefinition,
    *,
    tag_context: str = "admin",
) -> dict:
    row = get_automatic_message_row(db, definition.key)
    body = _normalize_body(row.body if row is not None else None)
    is_overridden = bool(row and row.is_overridden and body is not None)
    enabled = bool(row.enabled) if row is not None and row.enabled is not None else True
    notification_type = definition.key.rsplit(".", 1)[-1]
    template = body if is_overridden else ("" if row is not None and not enabled and body is None else definition.default_text)
    tag_items = notification_bot_basic_tag_items(notification_type, context=tag_context)
    return {
        "type": notification_type,
        "key": definition.key,
        "title": definition.title,
        "description": definition.description,
        "enabled": enabled,
        "template": normalize_notification_bot_basic_semantic_tag_labels(template, context=tag_context),
        "default_template": normalize_notification_bot_basic_semantic_tag_labels(definition.default_text, context=tag_context),
        "is_overridden": is_overridden,
        "html_enabled": bool(row.html_enabled) if row is not None and row.html_enabled is not None else definition.html_enabled,
        "disable_link_preview": bool(row.disable_link_preview) if row is not None else False,
        "send_silently": bool(row.send_silently) if row is not None else False,
        "tags": [item["label"] for item in tag_items],
        "tag_items": tag_items,
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
    }


def validate_notification_bot_basic_template(*, enabled: bool, template: str | None) -> str | None:
    body = _normalize_body(template)
    if enabled and body is None:
        raise ValueError("Добавьте текст уведомления или отключите этот тип")
    try:
        validate_telegram_html(body or "")
    except TelegramHtmlValidationError as exc:
        raise ValueError(str(exc) or "Некорректная Telegram-разметка") from exc
    return body


def upsert_notification_bot_basic_template(
    db: Session,
    notification_type: str,
    *,
    enabled: bool,
    template: str | None,
    disable_link_preview: bool = False,
    send_silently: bool = False,
    html_enabled: bool = True,
) -> AutomaticMessageTemplate:
    definition = get_notification_bot_basic_definition(notification_type)
    if definition is None:
        raise KeyError(notification_type)
    body = validate_notification_bot_basic_template(enabled=enabled, template=template)
    now = datetime.utcnow()
    row = get_automatic_message_row(db, definition.key)
    if row is None:
        row = AutomaticMessageTemplate(
            id=str(uuid.uuid4()),
            key=definition.key,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    row.body = body
    row.is_overridden = body is not None and body != definition.default_text
    row.html_enabled = bool(html_enabled)
    row.enabled = bool(enabled)
    row.disable_link_preview = bool(disable_link_preview)
    row.send_silently = bool(send_silently)
    row.updated_at = now
    return row


def upsert_automatic_message_override(db: Session, key: str, body: str | None, *, html_enabled: bool | None = None) -> AutomaticMessageTemplate:
    definition = get_automatic_message_definition(key)
    if definition is None:
        raise KeyError(key)
    now = datetime.utcnow()
    row = get_automatic_message_row(db, key)
    if row is None:
        row = AutomaticMessageTemplate(
            id=str(uuid.uuid4()),
            key=key,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    row.body = _normalize_body(body)
    row.is_overridden = row.body is not None
    row.html_enabled = definition.html_enabled if html_enabled is None else bool(html_enabled)
    row.updated_at = now
    return row


def reset_automatic_message_override(db: Session, key: str) -> None:
    row = get_automatic_message_row(db, key)
    if row is None:
        return
    row.body = None
    row.is_overridden = False
    row.updated_at = datetime.utcnow()
