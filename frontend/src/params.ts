import { defineParams } from '@sveltejs/kit/params';

export const params = defineParams({
	service: (param: string) => (/^[a-z0-9-]{1,40}$/.test(param) ? param : undefined)
});
