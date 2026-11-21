import { test as setup } from '@playwright/test';
import { EMAIL, OTHER_EMAIL, STATE, signIn } from './stack';

setup('sign in each viewer once', async ({ browser, baseURL }) => {
	for (const [email, path] of [
		[EMAIL, STATE.viewer],
		[OTHER_EMAIL, STATE.other]
	]) {
		const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true });
		await signIn(await context.newPage(), email);
		await context.storageState({ path });
		await context.close();
	}
});
