import { defineParams } from '@sveltejs/kit/params';

export const params = defineParams({
	service: (param: string) => (/^[a-z0-9-]{1,40}$/.test(param) ? param : undefined),
	uid: (param: string) => (/^[1-9][0-9]{0,18}$/.test(param) ? param : undefined)
});
