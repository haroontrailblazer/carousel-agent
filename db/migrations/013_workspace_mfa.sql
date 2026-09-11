-- Enforce opted-in second factors for both login and existing cookies.
BEGIN;
CREATE OR REPLACE FUNCTION public.carousel_provision_workspace(owner uuid,address text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actual text; active boolean;
BEGIN
 SELECT lower(email) INTO actual FROM auth.users WHERE id=owner AND email_confirmed_at IS NOT NULL;
 IF actual IS NULL OR actual<>lower(address) THEN RAISE EXCEPTION 'Verified account required' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM public.app_users WHERE lower(email)=actual AND disabled) THEN
  RETURN jsonb_build_object('enabled',false);
 END IF;
 INSERT INTO public.carousel_workspaces(id,email) VALUES(owner,actual)
  ON CONFLICT(id) DO UPDATE SET email=EXCLUDED.email;
 SELECT enabled INTO active FROM public.carousel_workspaces WHERE id=owner;
 RETURN jsonb_build_object('enabled',active,'requires_mfa',EXISTS(SELECT 1 FROM auth.mfa_factors WHERE user_id=owner AND status='verified'));
END $$;
NOTIFY pgrst,'reload schema';
COMMIT;
