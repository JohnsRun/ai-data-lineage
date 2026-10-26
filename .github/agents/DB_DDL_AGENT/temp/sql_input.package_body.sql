
  CREATE OR REPLACE EDITIONABLE PACKAGE BODY "DEV"."PKG_JTA_ERROR" 
IS

    /*
        Throw an exception, this makes coding a little simpler
    */
    PROCEDURE sp_throw (
        p_code IN NUMBER,
        p_message   IN VARCHAR2
    )
    IS
    BEGIN
        raise_application_error(p_code, p_message);
    END;

    /*
        Log the exception to an error table.

        Most procedures will do this when exceptions occur.
        For development we will show the error in console as well.
    */
    PROCEDURE sp_log_error (
      p_code IN NUMBER,
      p_message   IN VARCHAR2
    )
    IS
        -- autonomous transaction needed, otherwise rollback will remove log entry
        PRAGMA autonomous_transaction; 
    BEGIN
        -- show info in console, disable this line in production
        dbms_output.put_line('error logged: ' || p_message);
        -- log error into error table
        INSERT INTO jta_errors (error_id, date_time, user_name, code, message)
        VALUES (seq_error.NEXTVAL, sysdate, USER, p_code, p_message);
        COMMIT;
    END;

    /*
        Show an error in the console.

        Sometimes you don't want to log an error because it is not a
        note worthy failure, e.g. it is not a problem if no data was 
        found for a query.

        This procedure is available for testing purposes
    */
    PROCEDURE sp_show_in_console (
        p_code IN NUMBER := NULL,
        p_message IN VARCHAR2
    )
    IS
    BEGIN
        dbms_output.put_line('A trivial error occured: ' || p_message);        
    END;

END pkg_jta_error;
