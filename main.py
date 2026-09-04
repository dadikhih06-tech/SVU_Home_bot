"""
نقطة الدخول الرئيسية - بوت الخدمات الأكاديمية
كلية علوم الإدارة وإدارة الأعمال - الجامعة الافتراضية السورية
التسلسل: تخصص ← خدمة ← كتلة ← مواد (سلة مشتريات)
"""
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ConversationHandler
)

from config import BOT_TOKEN, ADMIN_IDS, ADMIN_GROUP_ID, MAIN_ADMIN_ID, logger, BACKUP_INTERVAL
from database import init_database, migrate_database, DB_PATH
from initial_data import populate_initial_data
from states import (
    WELCOME, CHOOSE_SPEC, CHOOSE_SERVICE, CHOOSE_BLOCK, CHOOSE_COURSES,
    CHOOSE_PAYMENT, GET_PAYMENT_INFO, CONFIRM_ORDER, SEARCH_COURSES, VIEW_CART,
    CONTENT_AREA, SELECT_SPEC, SELECT_SERVICE_ADMIN, SELECT_ACTION,
    AWAIT_INPUT, AWAIT_CONFIRMATION, ADMIN_BLOCK_SELECT,
    BLOCK_MANAGE_COURSES, AWAIT_NEW_COURSE_NAME, MANAGE_COURSES_PAGE,
    PAYMENT_MENU, SELECT_PAYMENT_ACTION, AWAIT_PAYMENT_NAME,
    AWAIT_PAYMENT_DETAILS, AWAIT_EDIT_PAYMENT_DETAILS,
    AWAIT_PAYMENT_CONFIRMATION, AWAIT_DELETE_CONFIRMATION,
    AWAIT_PAYMENT_IMAGE, ADMIN_BROADCAST, BROADCAST_PREVIEW, BROADCAST_SCHEDULE
)
from error_handler import global_error_handler
from data_manager import get_data_manager
from student_handlers import (
    start, main_menu_callback_handler, start_order_conversation,
    choose_spec_callback, choose_service_callback, choose_block_callback,
    choose_courses_callback, cart_callback_handler,
    choose_payment_callback, get_payment_info_callback,
    confirm_order_callback, cancel_confirm_callback,
    search_courses_handler, cancel, support_command
)
from admin_handlers import (
    admin_main_menu_callback, admin_toggle_maintenance,
    admin_stats_handler, admin_advanced_stats_handler,
    admin_reset_stats_handler, admin_confirm_reset_stats,
    export_data_command
)
from ban_manager import (
    ban_user_command, unban_user_command,
    manage_bans_handler, unban_user_callback
)
from admin_orders import (
    admin_action_callback, admin_reply_handler,
    complete_order_handler, admin_orders_menu,
    orders_list_callback, orders_view_callback,
    orders_action_callback
)
from admin_content import (
    admin_content_menu, content_area_handler,
    admin_spec_actions, admin_service_actions, admin_block_actions,
    admin_courses_page_handler, admin_toggle_course,
    admin_select_all_courses, admin_add_course_prompt,
    admin_delete_course_confirm, admin_handle_input,
    admin_end_conversation, admin_cancel
)
from admin_payment import (
    admin_payment_menu, admin_select_payment_method,
    admin_payment_action_handler, admin_payment_delete_confirm_handler,
    admin_add_payment_prompt_name, admin_handle_payment_name,
    admin_handle_payment_details, admin_handle_payment_image,
    admin_payment_end_conversation
)
from broadcast import (
    broadcast_start_handler, broadcast_preview_handler,
    broadcast_confirm_callback, broadcast_schedule_handler,
    broadcast_cancel_active, broadcast_cancel_command
)
from rate_limiter import get_rate_limiter
from rating_system import handle_rating_callback, show_ratings_stats
from notification_retry import NotificationRetryManager

import os


async def post_initialization(application: Application):
    if not os.path.exists(DB_PATH):
        db = await init_database()
        await db.close()
        logger.info("Database file created and initialized.")
    else:
        logger.info("Database file exists. Running schema migration...")

    if not await migrate_database():
        logger.error("Database migration FAILED! Retrying once...")
        import asyncio
        await asyncio.sleep(1)
        if not await migrate_database():
            logger.error("Database migration failed again!")

    await populate_initial_data()

    dm = get_data_manager()
    try:
        sent = await dm.send_backup_to_telegram(application.bot, MAIN_ADMIN_ID)
        if sent:
            logger.info(f"Startup backup sent to admin ({MAIN_ADMIN_ID})")
    except Exception as e:
        logger.warning(f"Could not send startup backup: {e}")

    prices = await dm.get_prices()
    specs = await dm.get_specializations()
    user_count = await dm.get_user_count()
    logger.info(f"Init complete. Specs: {len(specs)}, Services: {len(prices)}, "
                f"Users: {user_count}")


async def auto_backup_job(context):
    dm = get_data_manager()
    sent = await dm.send_backup_to_telegram(context.bot, MAIN_ADMIN_ID)
    if sent:
        logger.info("Auto-backup sent to admin")
    else:
        logger.warning("Auto-backup failed!")


async def retry_notifications_job(context):
    try:
        await NotificationRetryManager.process_retry_queue(context)
    except Exception as e:
        err = str(e).lower()
        if "no such table" in err:
            logger.warning(f"retry_notifications: table not ready - {e}")
        else:
            logger.error(f"retry_notifications_job error: {e}")


async def rate_limiter_cleanup_job(context):
    get_rate_limiter().cleanup()


async def post_shutdown(application: Application):
    logger.info("Bot shutting down...")
    from database import get_db_pool
    try:
        await get_db_pool().close()
    except Exception:
        pass


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_initialization)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_error_handler(global_error_handler)

    application.job_queue.run_repeating(
        auto_backup_job, interval=BACKUP_INTERVAL, first=BACKUP_INTERVAL,
        name="auto_backup")
    application.job_queue.run_repeating(
        retry_notifications_job, interval=60, first=60, name="retry_notifications")
    application.job_queue.run_repeating(
        rate_limiter_cleanup_job, interval=300, first=300, name="rate_limiter_cleanup")

    group_filter = filters.Chat(chat_id=ADMIN_GROUP_ID)
    admin_private_filter = filters.User(user_id=ADMIN_IDS) & filters.ChatType.PRIVATE

    # --- محادثة الطالب: تخصص ← خدمة ← كتلة ← مواد + سلة ---
    student_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_order_conversation, pattern="^start_order$")],
        states={
            WELCOME: [
                CallbackQueryHandler(
                    main_menu_callback_handler,
                    pattern=r"^(start_order|my_orders|my_profile|faq|back_to_start)$")
            ],
            CHOOSE_SPEC: [
                CallbackQueryHandler(
                    choose_spec_callback, pattern=r"^(spec_.*|back_to_start)$")
            ],
            CHOOSE_SERVICE: [
                CallbackQueryHandler(
                    choose_service_callback,
                    pattern=r"^(service_.*|back_to_services|back_to_start)$")
            ],
            CHOOSE_BLOCK: [
                CallbackQueryHandler(
                    choose_block_callback,
                    pattern=r"^(block_.*|view_cart|done_selecting_courses|"
                            r"back_to_services|back_to_start)$")
            ],
            CHOOSE_COURSES: [
                CallbackQueryHandler(
                    choose_courses_callback,
                    pattern=r"^(course_.*|view_cart|done_selecting_courses|"
                            r"back_to_blocks|search_courses|clear_search|"
                            r"clear_all_courses)$")
            ],
            SEARCH_COURSES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_courses_handler),
                CallbackQueryHandler(
                    choose_courses_callback,
                    pattern=r"^(course_.*|view_cart|done_selecting_courses|"
                            r"back_to_blocks|clear_search|clear_all_courses)$")
            ],
            VIEW_CART: [
                CallbackQueryHandler(
                    cart_callback_handler,
                    pattern=r"^(cart_remove_.*|view_cart|done_selecting_courses|"
                            r"back_to_blocks|clear_all_courses|cancel_order)$")
            ],
            CHOOSE_PAYMENT: [
                CallbackQueryHandler(
                    choose_payment_callback,
                    pattern=r"^(payment_.+|view_cart|cancel_order)$"),
                CallbackQueryHandler(
                    cancel_confirm_callback,
                    pattern=r"^(cancel_confirm_yes|cancel_confirm_no)$")
            ],
            GET_PAYMENT_INFO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND | filters.PHOTO | filters.Document.ALL,
                    get_payment_info_callback),
                CallbackQueryHandler(choose_payment_callback, pattern=r"^cancel_order$")
            ],
            CONFIRM_ORDER: [
                CallbackQueryHandler(
                    confirm_order_callback,
                    pattern=r"^(confirm_order_yes|confirm_order_no)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="user_conversation",
        persistent=False,
        allow_reentry=True
    )

    # --- محادثة إدارة المحتوى ---
    admin_content_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_content_menu, pattern="^admin_content_menu$")],
        states={
            CONTENT_AREA: [
                CallbackQueryHandler(content_area_handler, pattern="^area_"),
            ],
            SELECT_SPEC: [
                CallbackQueryHandler(admin_spec_actions,
                                     pattern=r"^(add_spec|delete_spec_|confirm_del_spec_yes)"),
            ],
            SELECT_SERVICE_ADMIN: [
                CallbackQueryHandler(admin_service_actions,
                                     pattern=r"^(add_service|sel_service_|action_|area_services)"),
            ],
            SELECT_ACTION: [
                CallbackQueryHandler(admin_service_actions,
                                     pattern=r"^(action_|area_services)"),
            ],
            ADMIN_BLOCK_SELECT: [
                CallbackQueryHandler(admin_block_actions,
                                     pattern=r"^(blocks_spec_|sel_block_|del_block_|"
                                             r"confirm_del_block_yes|add_block|"
                                             r"back_to_blocks_list|area_blocks)"),
            ],
            BLOCK_MANAGE_COURSES: [
                CallbackQueryHandler(admin_courses_page_handler, pattern="^courses_page_"),
                CallbackQueryHandler(admin_toggle_course, pattern="^toggle_course_"),
                CallbackQueryHandler(admin_select_all_courses, pattern="^select_all_courses$"),
                CallbackQueryHandler(admin_delete_course_confirm, pattern="^delete_course_"),
                CallbackQueryHandler(admin_add_course_prompt, pattern="^add_course$"),
                CallbackQueryHandler(admin_block_actions, pattern="^back_to_blocks_list$"),
            ],
            AWAIT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_input)],
            AWAIT_NEW_COURSE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_input)],
            AWAIT_CONFIRMATION: [
                CallbackQueryHandler(admin_end_conversation, pattern="^apply_and_end$")],
        },
        fallbacks=[
            CommandHandler("cancel", admin_cancel),
            CallbackQueryHandler(admin_end_conversation, pattern="^end_admin_convo$"),
        ],
        name="admin_content_conversation",
        persistent=False
    )

    # --- محادثة إدارة طرق الدفع ---
    admin_payment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_payment_menu, pattern="^admin_payment_menu$")],
        states={
            PAYMENT_MENU: [
                CallbackQueryHandler(admin_select_payment_method, pattern="^payment_select_"),
                CallbackQueryHandler(admin_add_payment_prompt_name, pattern="^payment_add$"),
            ],
            SELECT_PAYMENT_ACTION: [
                CallbackQueryHandler(admin_payment_action_handler, pattern="^payment_action_"),
                CallbackQueryHandler(admin_payment_menu, pattern="^payment_back_to_menu$"),
            ],
            AWAIT_PAYMENT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_payment_name)],
            AWAIT_PAYMENT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_payment_details)],
            AWAIT_EDIT_PAYMENT_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_payment_details)],
            AWAIT_PAYMENT_IMAGE: [
                MessageHandler(
                    filters.PHOTO | (filters.TEXT & filters.Regex('^/skip$')),
                    admin_handle_payment_image)],
            AWAIT_PAYMENT_CONFIRMATION: [
                CallbackQueryHandler(
                    admin_payment_end_conversation, pattern="^payment_apply_and_end$")],
            AWAIT_DELETE_CONFIRMATION: [
                CallbackQueryHandler(
                    admin_payment_delete_confirm_handler,
                    pattern="^payment_confirm_delete_yes$"),
                CallbackQueryHandler(admin_payment_menu, pattern="^payment_back_to_menu$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(admin_end_conversation, pattern="^end_admin_convo$"),
            CommandHandler("cancel", admin_cancel),
        ],
        name="admin_payment_conversation",
        persistent=False
    )

    # --- محادثة البث ---
    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(broadcast_start_handler, pattern="^admin_broadcast_start$")],
        states={
            ADMIN_BROADCAST: [
                MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_preview_handler)],
            BROADCAST_PREVIEW: [
                CallbackQueryHandler(broadcast_confirm_callback, pattern="^broadcast_")],
            BROADCAST_SCHEDULE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_schedule_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="admin_broadcast",
        persistent=False
    )

    # --- التسجيل (block=False للتوازي) ---

    # المجموعة -1: التقييم
    application.add_handler(
        CallbackQueryHandler(handle_rating_callback, pattern=r"^rate_[1-5]_", block=False),
        group=-1)
    application.add_handler(
        CallbackQueryHandler(handle_rating_callback, pattern=r"^rate_skip_", block=False),
        group=-1)
    application.add_handler(
        CallbackQueryHandler(show_ratings_stats, pattern=r"^admin_ratings_stats$", block=False),
        group=-1)

    # المجموعة 0: المحادثات
    application.add_handler(student_conv)
    application.add_handler(admin_content_conv)
    application.add_handler(admin_payment_conv)
    application.add_handler(broadcast_conv)

    # أوامر عامة
    application.add_handler(CommandHandler("start", start, block=False))
    application.add_handler(CallbackQueryHandler(
        main_menu_callback_handler,
        pattern=r"^(my_orders|my_profile|faq|back_to_start)$", block=False))
    application.add_handler(CommandHandler("support", support_command, block=False))

    # أوامر مجموعة الأدمن
    application.add_handler(CommandHandler("complete", complete_order_handler,
                                           filters=group_filter, block=False))
    application.add_handler(CommandHandler("ban", ban_user_command,
                                           filters=group_filter, block=False))
    application.add_handler(CommandHandler("unban", unban_user_command,
                                           filters=group_filter, block=False))

    # أوامر الأدمن الخاصة
    application.add_handler(CommandHandler("export", export_data_command,
                                           filters=admin_private_filter, block=False))
    application.add_handler(CommandHandler("cancel_broadcast", broadcast_cancel_command,
                                           filters=admin_private_filter, block=False))

    # أزرار عامة
    application.add_handler(CallbackQueryHandler(
        admin_main_menu_callback, pattern=r"^admin_main_menu$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_toggle_maintenance, pattern=r"^admin_toggle_maintenance$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_stats_handler, pattern=r"^admin_stats$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_reset_stats_handler, pattern=r"^admin_reset_stats$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_advanced_stats_handler, pattern=r"^admin_advanced_stats$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_confirm_reset_stats, pattern=r"^confirm_reset_stats$", block=False))
    application.add_handler(CallbackQueryHandler(
        manage_bans_handler, pattern=r"^admin_manage_bans$", block=False))
    application.add_handler(CallbackQueryHandler(
        unban_user_callback, pattern=r"^admin_unban_", block=False))
    application.add_handler(CallbackQueryHandler(
        broadcast_cancel_active, pattern="^broadcast_cancel_active$", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_orders_menu, pattern=r"^admin_orders_menu$", block=False))
    application.add_handler(CallbackQueryHandler(
        orders_list_callback, pattern=r"^orders_list_(new|open)_\d+$", block=False))
    application.add_handler(CallbackQueryHandler(
        orders_view_callback, pattern=r"^orders_view_", block=False))
    application.add_handler(CallbackQueryHandler(
        orders_action_callback, pattern=r"^orders_(remind_reply|complete)_", block=False))
    application.add_handler(CallbackQueryHandler(
        admin_action_callback, pattern=r"^admin_(approve|reject)_", block=False))
    application.add_handler(MessageHandler(
        filters.REPLY & group_filter, admin_reply_handler, block=False))

    logger.info("Starting SVU Management Sciences bot (specs + blocks + cart)...")
    application.run_polling()


if __name__ == "__main__":
    main()
